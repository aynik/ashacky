// SPDX-License-Identifier: GPL-2.0
/* LinuxHost wireless control frontend with a dedicated Ethernet packet path.
 * A root-only local bridge supplies actual host scan results for cfg80211.
 */
#include <linux/module.h>
#include <linux/etherdevice.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/rtnetlink.h>
#include <linux/if_arp.h>
#include <net/cfg80211.h>

#define LH_GET_SCAN _IOR('L', 1, __u32)
#define LH_FINISH_SCAN _IOW('L', 2, __u32)
#define LH_ABORT_SCAN _IOW('L', 3, __u32)
struct lh_connection {
	__le32 sequence;
	u8 operation, ssid_length, psk_length, reserved;
	u8 bssid[ETH_ALEN], ssid[32], psk[32];
} __packed;
struct lh_connection_result {
	__le32 sequence;
	__le16 status, reserved;
	u8 bssid[ETH_ALEN], padding[2];
} __packed;
#define LH_GET_CONNECTION _IOR('L', 4, struct lh_connection)
#define LH_CONNECTION_RESULT _IOW('L', 5, struct lh_connection_result)
struct lh_signal {
	u8 bssid[ETH_ALEN];
	s8 signal;
	u8 reserved;
} __packed;
#define LH_SIGNAL _IOW('L', 6, struct lh_signal)
struct lh_record {
	__le32 sequence, frequency, signal;
	__le16 capability, beacon_interval;
	u8 bssid[ETH_ALEN];
	__le16 ie_length;
} __packed;
struct lh_priv {
	struct wireless_dev wdev;
	struct net_device *netdev;
	struct cfg80211_scan_request *scan;
	struct delayed_work timeout;
	u32 sequence;
	unsigned long scan_deadline;
	struct lh_connection connection;
	struct delayed_work connection_timeout;
	u32 connection_sequence;
	unsigned long connection_deadline;
	u16 disconnect_reason;
	bool connected;
	u8 associated_bssid[ETH_ALEN];
	s8 signal;
	bool signal_valid;
	unsigned long signal_updated;
	struct net_device __rcu *lower;
	struct notifier_block lower_notifier;
};
static struct wiphy *radio;
static char *lowerdev;
module_param(lowerdev, charp, 0400);
MODULE_PARM_DESC(lowerdev, "Dedicated Ethernet transport; must not be a management interface");
static struct ieee80211_channel channels2[14], channels5[28];
static struct ieee80211_rate rates[] = {
	{ .bitrate = 10, .hw_value = 3 }, { .bitrate = 20, .hw_value = 4 },
	{ .bitrate = 55, .hw_value = 5 }, { .bitrate = 110, .hw_value = 6 },
	{ .bitrate = 60, .hw_value = 0 }, { .bitrate = 120, .hw_value = 1 },
	{ .bitrate = 240, .hw_value = 2 },
};
static struct ieee80211_supported_band bands[2];
static const u32 ciphers[] = { WLAN_CIPHER_SUITE_CCMP };

static void finish_connection(struct lh_priv *p, u16 status, const u8 *bssid)
{
	u8 operation = p->connection.operation;
	if (!operation) return;
	memzero_explicit(&p->connection, sizeof(p->connection));
	p->signal_valid = false;
	if (operation == 1) {
		WRITE_ONCE(p->connected, status == WLAN_STATUS_SUCCESS);
		if (p->connected) ether_addr_copy(p->associated_bssid, bssid);
		cfg80211_connect_result(p->netdev, bssid, NULL, 0, NULL, 0, status, GFP_KERNEL);
	} else {
		// Even if the host is unavailable, stop presenting a usable guest
		// link. Host disassociation failure remains visible to the bridge.
		if (p->connected)
			cfg80211_disconnected(p->netdev, p->disconnect_reason, NULL, 0, true, GFP_KERNEL);
		WRITE_ONCE(p->connected, false);
	}
	rcu_read_lock();
	if (p->connected && rcu_dereference(p->lower) && netif_carrier_ok(rcu_dereference(p->lower)))
		netif_carrier_on(p->netdev);
	else
		netif_carrier_off(p->netdev);
	rcu_read_unlock();
}
static void connection_timeout(struct work_struct *work)
{
	struct lh_priv *p = container_of(to_delayed_work(work), struct lh_priv, connection_timeout);
	wiphy_lock(radio);
	if (p->connection.operation && time_before(jiffies, p->connection_deadline))
		mod_delayed_work(system_wq, &p->connection_timeout, p->connection_deadline - jiffies);
	else
		finish_connection(p, WLAN_STATUS_UNSPECIFIED_FAILURE, NULL);
	wiphy_unlock(radio);
}
static void begin_connection(struct lh_priv *p, u8 operation)
{
	memzero_explicit(&p->connection, sizeof(p->connection));
	p->connection.operation = operation;
	if (!++p->connection_sequence) ++p->connection_sequence;
	p->connection.sequence = cpu_to_le32(p->connection_sequence);
	p->connection_deadline = jiffies + 90 * HZ;
	mod_delayed_work(system_wq, &p->connection_timeout, 90 * HZ);
}
static int connect_network(struct wiphy *wiphy, struct net_device *dev,
			   struct cfg80211_connect_params *sme)
{
	struct lh_priv *p = wiphy_priv(wiphy);
	if (!netif_running(dev)) return -ENETDOWN;
	if (p->scan || p->connection.operation || p->connected) return -EBUSY;
	if (!sme->ssid || !sme->ssid_len || sme->ssid_len > 32) return -EINVAL;
	if (sme->crypto.sae_pwd || sme->key_len) return -EOPNOTSUPP;
	if (sme->privacy && (!sme->crypto.psk ||
	    sme->crypto.wpa_versions != NL80211_WPA_VERSION_2 ||
	    sme->crypto.n_akm_suites != 1 || sme->crypto.akm_suites[0] != WLAN_AKM_SUITE_PSK))
		return -EOPNOTSUPP;
	begin_connection(p, 1);
	p->connection.ssid_length = sme->ssid_len;
	memcpy(p->connection.ssid, sme->ssid, sme->ssid_len);
	if (sme->bssid) ether_addr_copy(p->connection.bssid, sme->bssid);
	if (sme->crypto.psk) {
		p->connection.psk_length = 32;
		memcpy(p->connection.psk, sme->crypto.psk, 32);
	}
	return 0;
}
static int disconnect_network(struct wiphy *wiphy, struct net_device *dev, u16 reason)
{
	struct lh_priv *p = wiphy_priv(wiphy);
	if (p->connection.operation == 2) return -EBUSY;
	if (p->connection.operation == 1)
		finish_connection(p, WLAN_STATUS_UNSPECIFIED_FAILURE, NULL);
	p->disconnect_reason = reason;
	begin_connection(p, 2);
	return 0;
}

/* All scan state is serialized by the wiphy mutex, including misc operations. */
static void finish_scan(struct lh_priv *p, bool aborted)
{
	struct cfg80211_scan_info info = { .aborted = aborted };
	if (p->scan) {
		cfg80211_scan_done(p->scan, &info);
		p->scan = NULL;
	}
}
static void scan_timeout(struct work_struct *work)
{
	struct lh_priv *p = container_of(to_delayed_work(work), struct lh_priv, timeout);
	wiphy_lock(radio);
	// A prior scan's timeout may already be waiting for the mutex when a
	// new scan starts. Never let that old work abort the new request early.
	if (p->scan && time_before(jiffies, p->scan_deadline))
		mod_delayed_work(system_wq, &p->timeout, p->scan_deadline - jiffies);
	else
		finish_scan(p, true);
	wiphy_unlock(radio);
}
static int start_scan(struct wiphy *wiphy, struct cfg80211_scan_request *request)
{
	struct lh_priv *p = wiphy_priv(wiphy);
	if (p->scan) return -EBUSY;
	p->scan = request;
	p->scan_deadline = jiffies + 45 * HZ;
	if (!++p->sequence) ++p->sequence;
	mod_delayed_work(system_wq, &p->timeout, 45 * HZ);
	return 0;
}
static void abort_scan(struct wiphy *wiphy, struct wireless_dev *wdev)
{
	finish_scan(wiphy_priv(wiphy), true);
}
static int get_station(struct wiphy *wiphy, struct wireless_dev *dev,
		       const u8 *mac, struct station_info *info)
{
	struct lh_priv *p = wiphy_priv(wiphy);
	if (!p->connected || !ether_addr_equal(mac, p->associated_bssid)) return -ENOENT;
	if (p->signal_valid && time_before(jiffies, p->signal_updated + 15 * HZ)) {
		info->signal = p->signal;
		info->filled |= BIT_ULL(NL80211_STA_INFO_SIGNAL);
	}
	return 0;
}
static int dump_station(struct wiphy *wiphy, struct wireless_dev *dev,
			int idx, u8 *mac, struct station_info *info)
{
	struct lh_priv *p = wiphy_priv(wiphy);
	if (idx || !p->connected) return -ENOENT;
	ether_addr_copy(mac, p->associated_bssid);
	return get_station(wiphy, dev, mac, info);
}
static const struct cfg80211_ops wireless_ops = {
	.scan = start_scan, .abort_scan = abort_scan,
	.connect = connect_network, .disconnect = disconnect_network,
	.get_station = get_station, .dump_station = dump_station,
};
static long control(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct lh_priv *p = wiphy_priv(radio);
	struct lh_connection_result completion;
	struct lh_signal signal;
	u32 sequence;
	long result = 0;
	if (!capable(CAP_NET_ADMIN)) return -EPERM;
	if (cmd == LH_SIGNAL && copy_from_user(&signal, (void __user *)arg, sizeof(signal))) return -EFAULT;
	if (cmd == LH_CONNECTION_RESULT && copy_from_user(&completion, (void __user *)arg, sizeof(completion))) return -EFAULT;
	if ((cmd == LH_FINISH_SCAN || cmd == LH_ABORT_SCAN) &&
	    copy_from_user(&sequence, (void __user *)arg, sizeof(sequence))) return -EFAULT;
	wiphy_lock(radio);
	if (cmd == LH_GET_SCAN) {
		sequence = p->scan ? p->sequence : 0;
		if (copy_to_user((void __user *)arg, &sequence, sizeof(sequence))) result = -EFAULT;
	} else if (cmd == LH_FINISH_SCAN || cmd == LH_ABORT_SCAN) {
		if (!p->scan || sequence != p->sequence) result = -ESTALE;
		else finish_scan(p, cmd == LH_ABORT_SCAN);
	} else if (cmd == LH_GET_CONNECTION) {
		if (copy_to_user((void __user *)arg, &p->connection, sizeof(p->connection))) result = -EFAULT;
	} else if (cmd == LH_CONNECTION_RESULT) {
		if (!p->connection.operation || completion.sequence != p->connection.sequence) result = -ESTALE;
		else if (p->connection.operation == 1 && !le16_to_cpu(completion.status) && !is_valid_ether_addr(completion.bssid)) result = -EINVAL;
		else finish_connection(p, le16_to_cpu(completion.status), completion.bssid);
	} else if (cmd == LH_SIGNAL) {
		if (signal.reserved || signal.signal > 0 || signal.signal < -127) result = -EINVAL;
		else if (!p->connected || !ether_addr_equal(signal.bssid, p->associated_bssid)) result = -ESTALE;
		else {
			p->signal = signal.signal;
			p->signal_updated = jiffies;
			p->signal_valid = true;
		}
	} else result = -ENOTTY;
	wiphy_unlock(radio);
	return result;
}
static ssize_t publish(struct file *file, const char __user *buffer, size_t count, loff_t *pos)
{
	struct lh_priv *p = wiphy_priv(radio);
	struct lh_record *record;
	struct ieee80211_channel *channel;
	struct cfg80211_bss *bss;
	ssize_t result = count;
	s32 signal;
	u16 len;
	size_t offset;
	u8 *ies;
	if (!capable(CAP_NET_ADMIN)) return -EPERM;
	if (count < sizeof(*record) || count > sizeof(*record) + 4096) return -EINVAL;
	record = memdup_user(buffer, count);
	if (IS_ERR(record)) return PTR_ERR(record);
	len = le16_to_cpu(record->ie_length);
	signal = (s32)le32_to_cpu(record->signal);
	ies = (u8 *)(record + 1);
	if (sizeof(*record) + len != count || !is_valid_ether_addr(record->bssid) || signal > 0 || signal < -12700) {
		result = -EINVAL; goto out;
	}
	for (offset = 0; offset < len; offset += 2 + ies[offset + 1]) {
		if (offset + 2 > len || offset + 2 + ies[offset + 1] > len) {
			result = -EINVAL; goto out;
		}
	}
	wiphy_lock(radio);
	channel = ieee80211_get_channel(radio, le32_to_cpu(record->frequency));
	if (!p->scan || p->sequence != le32_to_cpu(record->sequence)) result = -ESTALE;
	else if (!channel) result = -EINVAL;
	else {
		bss = cfg80211_inform_bss(radio, channel, CFG80211_BSS_FTYPE_UNKNOWN,
			record->bssid, 0, le16_to_cpu(record->capability),
			le16_to_cpu(record->beacon_interval), ies, len, signal, GFP_KERNEL);
		if (!bss) result = -ENOMEM;
		else cfg80211_put_bss(radio, bss);
	}
	wiphy_unlock(radio);
out:
	kfree(record);
	return result;
}
static const struct file_operations bridge_ops = {
	.owner = THIS_MODULE, .unlocked_ioctl = control, .write = publish,
};
static struct miscdevice bridge = {
	.minor = MISC_DYNAMIC_MINOR, .name = "linuxhost-wifi", .mode = 0600,
	.fops = &bridge_ops,
};
static netdev_tx_t forward_packet(struct sk_buff *skb, struct net_device *dev)
{
	struct lh_priv *p = wiphy_priv(dev->ieee80211_ptr->wiphy);
	struct net_device *lower;
	rcu_read_lock();
	lower = rcu_dereference(p->lower);
	if (READ_ONCE(p->connected) && lower && netif_running(lower) && netif_carrier_ok(lower)) {
		skb->dev = lower;
		dev_queue_xmit(skb);
	} else {
		dev_kfree_skb(skb);
	}
	rcu_read_unlock();
	return NETDEV_TX_OK;
}
static rx_handler_result_t receive_packet(struct sk_buff **packet)
{
	struct sk_buff *skb = *packet;
	struct lh_priv *p = rcu_dereference(skb->dev->rx_handler_data);
	if (!READ_ONCE(p->connected) || !netif_running(p->netdev))
		return RX_HANDLER_PASS;
	skb = skb_share_check(skb, GFP_ATOMIC);
	if (!skb) return RX_HANDLER_CONSUMED;
	*packet = skb;
	skb->dev = p->netdev;
	return RX_HANDLER_ANOTHER;
}
/* Called under RTNL, before the lower device's final reference is released. */
static void detach_lower(struct lh_priv *p)
{
	struct net_device *lower = rtnl_dereference(p->lower);
	if (!lower) return;
	netif_carrier_off(p->netdev);
	RCU_INIT_POINTER(p->lower, NULL);
	netdev_rx_handler_unregister(lower);
	netdev_upper_dev_unlink(lower, p->netdev);
	synchronize_net();
	dev_put(lower);
}
static int lower_event(struct notifier_block *block, unsigned long event, void *data)
{
	struct lh_priv *p = container_of(block, struct lh_priv, lower_notifier);
	struct net_device *lower = netdev_notifier_info_to_dev(data);
	if (lower != rtnl_dereference(p->lower)) return NOTIFY_DONE;
	if (event == NETDEV_UNREGISTER) detach_lower(p);
	else if (event == NETDEV_CHANGE || event == NETDEV_DOWN || event == NETDEV_UP) {
		if (READ_ONCE(p->connected) && netif_running(lower) && netif_carrier_ok(lower))
			netif_carrier_on(p->netdev);
		else netif_carrier_off(p->netdev);
	}
	return NOTIFY_DONE;
}
static int attach_lower(struct lh_priv *p)
{
	struct net_device *lower;
	int error;
	if (!lowerdev || !*lowerdev) return 0; // Control-only probe remains supported.
	rtnl_lock();
	lower = dev_get_by_name(&init_net, lowerdev);
	if (!lower) { error = -ENODEV; goto out; }
	if (lower == p->netdev || lower->type != ARPHRD_ETHER || lower->ieee80211_ptr) {
		error = -EINVAL; goto put;
	}
	error = netdev_upper_dev_link(lower, p->netdev, NULL);
	if (error) goto put;
	error = netdev_rx_handler_register(lower, receive_packet, p);
	if (error) { netdev_upper_dev_unlink(lower, p->netdev); goto put; }
	eth_hw_addr_set(p->netdev, lower->dev_addr);
	p->netdev->mtu = lower->mtu;
	rcu_assign_pointer(p->lower, lower);
	rtnl_unlock();
	return 0;
put:
	dev_put(lower);
out:
	rtnl_unlock(); return error;
}
/* Called under RTNL, before cfg80211 handles NETDEV_DOWN and frees its
 * outstanding scan request. Late host replies must see no pending request.
 */
static int stop_device(struct net_device *dev)
{
	struct lh_priv *p = wiphy_priv(dev->ieee80211_ptr->wiphy);
	wiphy_lock(radio);
	finish_scan(p, true);
	finish_connection(p, WLAN_STATUS_UNSPECIFIED_FAILURE, NULL);
	if (p->connected) {
		cfg80211_disconnected(dev, WLAN_REASON_DEAUTH_LEAVING,
				      NULL, 0, true, GFP_KERNEL);
		WRITE_ONCE(p->connected, false);
	}
	p->signal_valid = false;
	netif_carrier_off(dev);
	wiphy_unlock(radio);
	/* Workers take the wiphy mutex, so never wait for them while holding it. */
	cancel_delayed_work_sync(&p->timeout);
	cancel_delayed_work_sync(&p->connection_timeout);
	return 0;
}
static const struct net_device_ops device_ops = {
	.ndo_start_xmit = forward_packet,
	.ndo_stop = stop_device,
};
static int __init lh_init(void)
{
	struct lh_priv *p;
	int i, error;
	const int five[] = {36,40,44,48,52,56,60,64,100,104,108,112,116,120,124,128,132,136,140,144,149,153,157,161,165,169,173,177};
	for (i = 0; i < 14; ++i) {
		channels2[i].band = NL80211_BAND_2GHZ;
		channels2[i].center_freq = i == 13 ? 2484 : 2412 + i * 5;
		channels2[i].hw_value = i + 1;
	}
	for (i = 0; i < ARRAY_SIZE(five); ++i) {
		channels5[i].band = NL80211_BAND_5GHZ;
		channels5[i].center_freq = 5000 + five[i] * 5;
		channels5[i].hw_value = five[i];
	}
	bands[0].channels = channels2; bands[0].n_channels = ARRAY_SIZE(channels2);
	bands[1].channels = channels5; bands[1].n_channels = ARRAY_SIZE(channels5);
	bands[0].band = NL80211_BAND_2GHZ;
	bands[0].bitrates = rates; bands[0].n_bitrates = ARRAY_SIZE(rates);
	bands[1].band = NL80211_BAND_5GHZ;
	bands[1].bitrates = rates + 4; bands[1].n_bitrates = ARRAY_SIZE(rates) - 4;
	radio = wiphy_new(&wireless_ops, sizeof(*p));
	if (!radio) return -ENOMEM;
	radio->interface_modes = BIT(NL80211_IFTYPE_STATION);
	radio->max_scan_ssids = 1;
	radio->signal_type = CFG80211_SIGNAL_TYPE_MBM;
	radio->cipher_suites = ciphers;
	radio->n_cipher_suites = ARRAY_SIZE(ciphers);
	wiphy_ext_feature_set(radio, NL80211_EXT_FEATURE_4WAY_HANDSHAKE_STA_PSK);
	radio->bands[NL80211_BAND_2GHZ] = &bands[0];
	radio->bands[NL80211_BAND_5GHZ] = &bands[1];
	p = wiphy_priv(radio);
	INIT_DELAYED_WORK(&p->timeout, scan_timeout);
	INIT_DELAYED_WORK(&p->connection_timeout, connection_timeout);
	p->netdev = alloc_netdev(0, "lhwifi%d", NET_NAME_ENUM, ether_setup);
	if (!p->netdev) { error = -ENOMEM; goto free_radio; }
	p->wdev.wiphy = radio; p->wdev.iftype = NL80211_IFTYPE_STATION; p->wdev.netdev = p->netdev;
	p->netdev->ieee80211_ptr = &p->wdev; p->netdev->netdev_ops = &device_ops;
	eth_hw_addr_random(p->netdev); netif_carrier_off(p->netdev);
	error = wiphy_register(radio); if (error) goto free_net;
	SET_NETDEV_DEV(p->netdev, wiphy_dev(radio));
	error = register_netdev(p->netdev); if (error) goto unregister_radio;
	p->lower_notifier.notifier_call = lower_event;
	error = register_netdevice_notifier(&p->lower_notifier); if (error) goto unregister_net;
	error = attach_lower(p); if (error) goto unregister_notifier;
	error = misc_register(&bridge); if (error) goto detach;
	return 0;
detach:
	rtnl_lock(); detach_lower(p); rtnl_unlock();
unregister_notifier:
	unregister_netdevice_notifier(&p->lower_notifier);
unregister_net:
	unregister_netdev(p->netdev);
unregister_radio:
	wiphy_unregister(radio);
free_net:
	free_netdev(p->netdev);
free_radio:
	wiphy_free(radio); return error;
}
static void __exit lh_exit(void)
{
	struct lh_priv *p = wiphy_priv(radio);
	misc_deregister(&bridge);
	rtnl_lock(); detach_lower(p); rtnl_unlock();
	unregister_netdevice_notifier(&p->lower_notifier);
	unregister_netdev(p->netdev);
	cancel_delayed_work_sync(&p->timeout);
	cancel_delayed_work_sync(&p->connection_timeout);
	memzero_explicit(&p->connection, sizeof(p->connection));
	wiphy_unregister(radio);
	free_netdev(p->netdev); wiphy_free(radio);
}
module_init(lh_init); module_exit(lh_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("LinuxHost cfg80211 host control and Ethernet transport prototype");
