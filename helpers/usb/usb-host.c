#include <libusb.h>
#include <usbredirhost.h>
#include <SystemConfiguration/SystemConfiguration.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <libproc.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "installation.h"
#define ENDPOINT ASHACKY_USB_DIRECTORY "/redirect.sock"
/* Refuse legacy peers whose automatic port allocation creates a USB 1.1 hub. */
static int direct_ports(int fd){
 pid_t pid=0;socklen_t len=sizeof(pid);char executable[PROC_PIDPATHINFO_MAXSIZE];
 if(getsockopt(fd,SOL_LOCAL,LOCAL_PEERPID,&pid,&len)||!proc_pidpath(pid,executable,sizeof(executable)))return 0;
 const char *base=strrchr(executable,'/');if(!base||strcmp(base+1,"qemu-system-aarch64"))return 0;
 int mib[3]={CTL_KERN,KERN_PROCARGS2,pid};size_t size=1024*1024;char *data=malloc(size);if(!data)return 0;
 if(sysctl(mib,3,data,&size,NULL,0)||size<sizeof(int)){free(data);return 0;}
 int argc=0;memcpy(&argc,data,sizeof(argc));char *p=data+sizeof(argc),*end=data+size;
 while(p<end&&*p)p++;while(p<end&&!*p)p++;
 unsigned ports=0;int redirs=0;
 for(int i=0;i<argc&&p<end;i++){
  size_t n=strnlen(p,end-p);if(p+n>=end)break;
  if(!strncmp(p,"usb-redir,",10)){
   redirs++;char *port=strstr(p,",port=");
   if(port&&port[6]>='1'&&port[6]<='4'&&(port[7]==0||port[7]==','))ports|=1u<<(port[6]-'1');
  }
  p+=n+1;
 }
 free(data);return redirs==4&&ports==15;
}
static volatile sig_atomic_t stopping=0;
static void stop_signal(int sig){stopping=1;}
static int active(void){uid_t uid=0;CFStringRef u=SCDynamicStoreCopyConsoleUser(NULL,&uid,NULL);if(u)CFRelease(u);return uid==ASHACKY_USER_ID;}
static void logmsg(void *p,int level,const char *msg){if(level<=2)fprintf(stderr,"USB: %s\n",msg);}
static int rd(void *p,uint8_t *b,int n){int r=(int)read(*(int*)p,b,n);if(r<0&&(errno==EAGAIN||errno==EINTR))return 0;return r==0?-1:r;}
static int wr(void *p,uint8_t *b,int n){int r=(int)write(*(int*)p,b,n);if(r<0&&(errno==EAGAIN||errno==EINTR))return 0;return r;}
static int storage(libusb_device *d){
 struct libusb_device_descriptor desc;struct libusb_config_descriptor *cfg=NULL;
 if(libusb_get_device_descriptor(d,&desc)||desc.idVendor==0x05ac)return 0;
 if(libusb_get_config_descriptor(d,0,&cfg))return 0;
 int ok=cfg->bNumInterfaces>0;
 for(int i=0;i<cfg->bNumInterfaces;i++)for(int j=0;j<cfg->interface[i].num_altsetting;j++)if(cfg->interface[i].altsetting[j].bInterfaceClass!=8)ok=0;
 libusb_free_config_descriptor(cfg);return ok;
}
static libusb_device_handle *choose(libusb_context *ctx,int *lock){
 libusb_device **list=NULL;ssize_t n=libusb_get_device_list(ctx,&list);libusb_device_handle *h=NULL;
 for(ssize_t i=0;i<n;i++){
  if(!storage(list[i]))continue;
  char path[160];uint8_t ports[8];int count=libusb_get_port_numbers(list[i],ports,8);
  if(count<=0)continue;
  int used=snprintf(path,sizeof(path),ASHACKY_USB_DIRECTORY "/port-%u",libusb_get_bus_number(list[i]));
  for(int j=0;j<count;j++)used+=snprintf(path+used,sizeof(path)-used,"-%u",ports[j]);
  int fd=open(path,O_CREAT|O_RDWR|O_NOFOLLOW,0600);if(fd<0)continue;
  if(flock(fd,LOCK_EX|LOCK_NB)){close(fd);continue;}
  if(libusb_open(list[i],&h)){close(fd);continue;}
  libusb_set_auto_detach_kernel_driver(h,1);*lock=fd;break;
 }
 if(list)libusb_free_device_list(list,1);return h;
}
static int present(libusb_context *ctx,libusb_device *device){
 libusb_device **list=NULL;ssize_t n=libusb_get_device_list(ctx,&list);int found=n<0;
 for(ssize_t i=0;i<n;i++)if(list[i]==device){found=1;break;}
 if(list)libusb_free_device_list(list,1);return found;
}
static void serve(int fd){
 libusb_context *ctx=NULL;if(libusb_init(&ctx))return;
 fcntl(fd,F_SETFL,O_NONBLOCK);int lock=-1,attached=0;libusb_device *current=NULL;time_t checked=0;
 struct usbredirhost *host=usbredirhost_open(ctx,NULL,logmsg,rd,wr,&fd,"LinuxHost USB",2,0);
 if(!host){libusb_exit(ctx);return;}
 time_t scanned=0;
 while(!stopping && active()){
  if(attached && time(NULL)!=checked){checked=time(NULL);
   if(!present(ctx,current)){
    fprintf(stderr,"Storage removed; releasing claim\n");usbredirhost_set_device(host,NULL);attached=0;
    libusb_unref_device(current);current=NULL;if(lock>=0)close(lock);lock=-1;
   }
  }
  if(!attached && time(NULL)!=scanned){scanned=time(NULL);libusb_device_handle *h=choose(ctx,&lock);
   if(h){current=libusb_ref_device(libusb_get_device(h));int r=usbredirhost_set_device(host,h);if(!r){attached=1;fprintf(stderr,"Storage attached\n");}else{libusb_unref_device(current);current=NULL;close(lock);lock=-1;}}}
  fd_set readset;FD_ZERO(&readset);FD_SET(fd,&readset);struct timeval wait={0,attached?1000:10000};
  select(fd+1,&readset,NULL,NULL,&wait);
  /* Also consumes pending device-lost notifications when the guest is idle. */
  {int r=usbredirhost_read_guest_data(host);
   if(r==-4||r==-3){usbredirhost_set_device(host,NULL);attached=0;if(current)libusb_unref_device(current);current=NULL;if(lock>=0)close(lock);lock=-1;}
   else if(r<0)break;
  }
  struct timeval zero={0,0};libusb_handle_events_timeout(ctx,&zero);
  if(usbredirhost_has_data_to_write(host)&&usbredirhost_write_guest_data(host)<0)break;
 }
 usbredirhost_close(host);if(current)libusb_unref_device(current);if(lock>=0)close(lock);libusb_exit(ctx);
}
int main(void){
 if(geteuid()!=0)return 1;struct sigaction stop={0};stop.sa_handler=stop_signal;sigemptyset(&stop.sa_mask);sigaction(SIGTERM,&stop,NULL);sigaction(SIGINT,&stop,NULL);signal(SIGPIPE,SIG_IGN);signal(SIGCHLD,SIG_IGN);umask(0077);
 mkdir(ASHACKY_USB_DIRECTORY,0755);chmod(ASHACKY_USB_DIRECTORY,0755);
 int fd=socket(AF_UNIX,SOCK_STREAM,0);struct sockaddr_un a={0};a.sun_family=AF_UNIX;strlcpy(a.sun_path,ENDPOINT,sizeof(a.sun_path));unlink(ENDPOINT);
 if(bind(fd,(struct sockaddr*)&a,sizeof(a))||listen(fd,4))return 2;
 chown(ENDPOINT,ASHACKY_USER_ID,ASHACKY_GROUP_ID);chmod(ENDPOINT,0600);
 while(!stopping){int client=accept(fd,NULL,NULL);if(client<0)continue;uid_t uid;gid_t gid;
  if(getpeereid(client,&uid,&gid)||uid!=ASHACKY_USER_ID||!active()){close(client);continue;}
  if(!direct_ports(client)){static time_t logged=0;if(time(NULL)-logged>30){fprintf(stderr,"USB waiting for QEMU with four explicit root ports\n");logged=time(NULL);}close(client);continue;}
  pid_t pid=fork();if(pid==0){close(fd);serve(client);close(client);_exit(0);}close(client);
 }
}
