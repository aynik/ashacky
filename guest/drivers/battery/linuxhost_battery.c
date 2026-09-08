// SPDX-License-Identifier: GPL-2.0
/* Host telemetry exposed through Linux's standard battery/AC power supply ABI. */
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/power_supply.h>
#include <linux/mutex.h>
#include <linux/workqueue.h>
static DEFINE_MUTEX(lock);
static struct platform_device *pdev;
static struct power_supply *bat, *ac;
static int present, online, capacity, charging;
static unsigned long updated;
static enum power_supply_property bp[]={POWER_SUPPLY_PROP_STATUS,POWER_SUPPLY_PROP_PRESENT,POWER_SUPPLY_PROP_CAPACITY,POWER_SUPPLY_PROP_TECHNOLOGY,POWER_SUPPLY_PROP_SCOPE,POWER_SUPPLY_PROP_MODEL_NAME};
static enum power_supply_property ap[]={POWER_SUPPLY_PROP_ONLINE};
static int get(struct power_supply *s,enum power_supply_property p,union power_supply_propval *v){
 mutex_lock(&lock);
 switch(p){
 case POWER_SUPPLY_PROP_STATUS:v->intval=!present?POWER_SUPPLY_STATUS_UNKNOWN:charging?POWER_SUPPLY_STATUS_CHARGING:!online?POWER_SUPPLY_STATUS_DISCHARGING:capacity==100?POWER_SUPPLY_STATUS_FULL:POWER_SUPPLY_STATUS_NOT_CHARGING;break;
 case POWER_SUPPLY_PROP_PRESENT:v->intval=present;break;
 case POWER_SUPPLY_PROP_CAPACITY:if(!present){mutex_unlock(&lock);return -ENODATA;}v->intval=capacity;break;
 case POWER_SUPPLY_PROP_ONLINE:v->intval=online;break;
 case POWER_SUPPLY_PROP_TECHNOLOGY:v->intval=POWER_SUPPLY_TECHNOLOGY_LION;break;
 case POWER_SUPPLY_PROP_SCOPE:v->intval=POWER_SUPPLY_SCOPE_SYSTEM;break;
 case POWER_SUPPLY_PROP_MODEL_NAME:v->strval="MacBook Air Battery";break;
 default:mutex_unlock(&lock);return -EINVAL;
 }
 mutex_unlock(&lock);return 0;
}
static ssize_t state_store(struct device *d,struct device_attribute *a,const char *buf,size_t n){
 int p,o,c,h;char extra;bool changed;
 if(sscanf(buf,"%d %d %d %d %c",&p,&o,&c,&h,&extra)!=4||p<0||p>1||o<0||o>1||c<0||c>100||h<0||h>1)return -EINVAL;
 mutex_lock(&lock);changed=p!=present||o!=online||c!=capacity||h!=charging;present=p;online=o;capacity=c;charging=h;updated=jiffies;mutex_unlock(&lock);
 if(changed){power_supply_changed(bat);power_supply_changed(ac);}return n;
}
static DEVICE_ATTR_WO(state);
static void expire(struct work_struct *work);
static DECLARE_DELAYED_WORK(expiry,expire);
static void expire(struct work_struct *work){bool changed=false;
 mutex_lock(&lock);if(present&&time_after(jiffies,updated+30*HZ)){present=0;changed=true;}mutex_unlock(&lock);
 if(changed)power_supply_changed(bat);
 schedule_delayed_work(&expiry,5*HZ);
}
static const struct power_supply_desc bd={.name="BAT0",.type=POWER_SUPPLY_TYPE_BATTERY,.properties=bp,.num_properties=ARRAY_SIZE(bp),.get_property=get};
static const struct power_supply_desc ad={.name="AC",.type=POWER_SUPPLY_TYPE_MAINS,.properties=ap,.num_properties=ARRAY_SIZE(ap),.get_property=get};
static int __init init(void){int e;struct power_supply_config c={};
 pdev=platform_device_register_simple("linuxhost-battery",-1,NULL,0);if(IS_ERR(pdev))return PTR_ERR(pdev);
 bat=power_supply_register(&pdev->dev,&bd,&c);if(IS_ERR(bat)){e=PTR_ERR(bat);goto dev;}
 ac=power_supply_register(&pdev->dev,&ad,&c);if(IS_ERR(ac)){e=PTR_ERR(ac);goto battery;}
 e=device_create_file(&pdev->dev,&dev_attr_state);if(e)goto mains;
 schedule_delayed_work(&expiry,5*HZ);return 0;
 mains:power_supply_unregister(ac);
 battery:power_supply_unregister(bat);
 dev:platform_device_unregister(pdev);return e;
}
static void __exit fini(void){cancel_delayed_work_sync(&expiry);device_remove_file(&pdev->dev,&dev_attr_state);power_supply_unregister(ac);power_supply_unregister(bat);platform_device_unregister(pdev);}
module_init(init);module_exit(fini);MODULE_LICENSE("GPL");MODULE_DESCRIPTION("LinuxHost host battery and AC telemetry");
