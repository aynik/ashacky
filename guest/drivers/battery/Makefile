obj-m += linuxhost_battery.o
all:
	$(MAKE) -C /lib/modules/$(KERNELRELEASE)/build M=$(PWD) modules
clean:
	$(MAKE) -C /lib/modules/$(KERNELRELEASE)/build M=$(PWD) clean
