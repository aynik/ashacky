// SPDX-License-Identifier: GPL-2.0-or-later
/* Host-owned Bluetooth radio state, independent of guest HCI/input traffic. */
#include <linux/module.h>
#include <linux/rfkill.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/capability.h>
#include <linux/workqueue.h>
#include <linux/mutex.h>

#define LH_BT_STATE _IOW('L', 0x20, __u32)
static struct rfkill *radio;
static DEFINE_MUTEX(state_lock);
static bool available, powered;
static unsigned long refreshed;
static struct delayed_work expiry;

static int set_block(void *unused, bool blocked)
{
    if (!READ_ONCE(available))
        return 0;
    /* No pretend power operation: only an already-matching state succeeds. */
    return blocked == !READ_ONCE(powered) ? 0 : -EOPNOTSUPP;
}
static const struct rfkill_ops radio_ops = { .set_block = set_block };

static void expire_state(struct work_struct *work)
{
    mutex_lock(&state_lock);
    if (available && time_after(jiffies, refreshed + 15 * HZ)) {
        available = false;
        rfkill_set_hw_state(radio, true);
    }
    mutex_unlock(&state_lock);
    schedule_delayed_work(&expiry, HZ);
}

static long control_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    __u32 state;
    if (!capable(CAP_NET_ADMIN))
        return -EPERM;
    if (cmd != LH_BT_STATE)
        return -ENOTTY;
    if (copy_from_user(&state, (void __user *)arg, sizeof(state)))
        return -EFAULT;
    /* bit0 = host backend available, bit1 = host radio powered */
    if (state > 3 || state == 2)
        return -EINVAL;
    mutex_lock(&state_lock);
    available = state & 1;
    powered = state & 2;
    refreshed = jiffies;
    rfkill_set_hw_state(radio, !available);
    rfkill_set_sw_state(radio, !powered);
    mutex_unlock(&state_lock);
    return 0;
}
static const struct file_operations control_ops = {
    .owner = THIS_MODULE, .unlocked_ioctl = control_ioctl,
    .compat_ioctl = control_ioctl,
};
static struct miscdevice control = {
    .minor = MISC_DYNAMIC_MINOR, .name = "linuxhost-bt-radio",
    .fops = &control_ops, .mode = 0600,
};
static int __init radio_init(void)
{
    int err;
    radio = rfkill_alloc("Mac Bluetooth", NULL, RFKILL_TYPE_BLUETOOTH, &radio_ops, NULL);
    if (!radio) return -ENOMEM;
    rfkill_init_sw_state(radio, true);
    rfkill_set_hw_state(radio, true);
    err = rfkill_register(radio);
    if (err) goto destroy;
    err = misc_register(&control);
    if (err) goto unregister;
    INIT_DELAYED_WORK(&expiry, expire_state);
    schedule_delayed_work(&expiry, HZ);
    return 0;
unregister:
    rfkill_unregister(radio);
destroy:
    rfkill_destroy(radio);
    return err;
}
static void __exit radio_exit(void)
{
    misc_deregister(&control);
    cancel_delayed_work_sync(&expiry);
    rfkill_unregister(radio);
    rfkill_destroy(radio);
}
module_init(radio_init);
module_exit(radio_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("LinuxHost host-owned Bluetooth radio state");
