#include <sys/mman.h>
#include <stdatomic.h>
#include <fcntl.h>
/* LinuxHost VideoToolbox decode broker. SPDX-License-Identifier: LGPL-2.1-or-later */
#include <libavcodec/avcodec.h>
#include <libavutil/opt.h>
#include <libavutil/mem.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netinet/tcp.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <lz4.h>
/* Private same-machine ABI, little-endian uint32 header: operation,width,height,length.
   Reply: status,width,height,length then tightly packed NV12. Bound all input. */
static int io(int fd, void *p, size_t n,int out){while(n){ssize_t r=out?write(fd,p,n):read(fd,p,n);if(r<0&&errno==EINTR)continue;if(r<=0)return -1;p=(char*)p+r;n-=r;}return 0;}
static void client(int fd){unsigned char *shared=MAP_FAILED;unsigned direct_frames=0;int direct=0;int remote=-1;AVCodecContext*c=NULL;AVFrame*f=av_frame_alloc();uint32_t h[4],r[4];
while(!io(fd,h,sizeof h,0)){
 if(h[3]>16*1024*1024)break;
 uint8_t*b=av_mallocz(h[3]+AV_INPUT_BUFFER_PADDING_SIZE);if(!b||io(fd,b,h[3],0)){av_free(b);break;}
 if(h[0]==3||h[0]==4||remote>=0){
  if(h[0]==4){if(remote>=0){av_free(b);break;}direct=1;h[0]=3;}
  if(remote<0){const char *path=getenv("LH_VIDEO_SHMEM_RESOURCE");if(!path)path="/run/linuxhost-video/resource";int mem=open(path,O_RDONLY|O_CLOEXEC);if(mem>=0){shared=mmap(NULL,64u*1024*1024,PROT_READ,MAP_SHARED,mem,0);close(mem);}if(shared!=MAP_FAILED)fprintf(stderr,"VP9 shared-memory transport active\n");remote=socket(AF_INET,SOCK_STREAM,0);struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(shared==MAP_FAILED?5558:5569),.sin_addr.s_addr=htonl(INADDR_LOOPBACK)};const char*host=getenv("LH_VIDEO_VP9_HOST");if(host&&inet_pton(AF_INET,host,&a.sin_addr)!=1){av_free(b);break;}int one=1;setsockopt(remote,IPPROTO_TCP,TCP_NODELAY,&one,sizeof one);struct timeval tv={.tv_sec=5};setsockopt(remote,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof tv);setsockopt(remote,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof tv);if(connect(remote,(void*)&a,sizeof a)){av_free(b);break;}}
  if(io(remote,h,16,1)||io(remote,b,h[3],1)||io(remote,r,16,0)){av_free(b);break;}av_free(b);
  if(r[0]==2){uint32_t desc[4];size_t expected=(size_t)r[1]*r[2]*3/2;
   if(shared==MAP_FAILED||r[3]!=16||io(remote,desc,16,0)||desc[1]||desc[0]%(16u*1024*1024)||desc[0]>48u*1024*1024||desc[2]>16u*1024*1024||desc[3]!=h[1]||r[1]>4096||r[2]>4096||(desc[2]&&desc[2]!=expected&&desc[2]!=expected*2))break;
   if(direct){if(io(fd,r,16,1)||io(fd,desc,16,1))break;direct_frames++;continue;}
   atomic_thread_fence(memory_order_acquire);r[0]=0;r[3]=desc[2];
   if(io(fd,r,16,1)||io(fd,shared+desc[0],r[3],1))break;
   continue;
  }
  if(r[3]>64*1024*1024)break;uint8_t*out=malloc(r[3]?r[3]:1);if(!out)break;int err=io(remote,out,r[3],0);if(!err&&r[0]==1){uint32_t raw=0;if(r[3]>=4)memcpy(&raw,out,4);if(!raw||raw>64*1024*1024){free(out);break;}uint8_t*plain=malloc(raw);if(!plain){free(out);break;}int n=LZ4_decompress_safe((char*)out+4,(char*)plain,r[3]-4,raw);free(out);out=plain;if(n!=(int)raw){free(out);break;}r[3]=raw;r[0]=0;}err=err||io(fd,r,16,1)||io(fd,out,r[3],1);free(out);if(err||r[0])break;continue;
 }
 int e=-1;uint8_t*out=NULL;memset(r,0,sizeof r);
 if(h[0]==1&&!c&&h[1]>0&&h[2]>0&&h[1]<=4096&&h[2]<=4096){
  c=avcodec_alloc_context3(avcodec_find_decoder_by_name("h264_videotoolbox_remote"));
  if(c){c->width=h[1];c->height=h[2];c->extradata=b;c->extradata_size=h[3];b=NULL;c->pkt_timebase=(AVRational){1,1000000};
   const char*host=getenv("LH_VIDEO_H264_HOST");av_opt_set(c->priv_data,"vt_remote_host",host?host:"127.0.0.1:5557",0);
   av_opt_set_int(c->priv_data,"vt_remote_decode_async",0,0);
   av_opt_set_int(c->priv_data,"vt_remote_timeout_ms",3000,0);
   e=avcodec_open2(c,c->codec,NULL);}
 }else if(h[0]==2&&c){AVPacket*p=av_packet_alloc();p->data=b;p->size=h[3];p->pts=h[1];p->dts=h[1];
  e=avcodec_send_packet(c,p);p->data=NULL;p->size=0;av_packet_free(&p);
  if(e>=0)e=avcodec_receive_frame(c,f);
  if(e>=0&&f->format==AV_PIX_FMT_NV12){r[1]=f->width;r[2]=f->height;r[3]=r[1]*r[2]*3/2;out=malloc(r[3]);
   if(!out)e=-1;else {for(unsigned y=0;y<r[2];y++)memcpy(out+y*r[1],f->data[0]+y*f->linesize[0],r[1]);
   for(unsigned y=0;y<r[2]/2;y++)memcpy(out+r[1]*r[2]+y*r[1],f->data[1]+y*f->linesize[1],r[1]);}}
  else if(e>=0)e=-1;av_frame_unref(f);
 }
 av_free(b);r[0]=(uint32_t)e;if(e<0){char err[128];av_strerror(e,err,sizeof err);fprintf(stderr,"decode failed: %s\n",err);r[3]=0;}
 if(io(fd,r,sizeof r,1)|| (r[3]&&io(fd,out,r[3],1))){free(out);break;}free(out);if(e<0)break;
}if(direct)fprintf(stderr,"VA direct shared frames=%u pixel_socket_bytes=0 descriptor_bytes=%u\n",direct_frames,direct_frames*32);if(shared!=MAP_FAILED)munmap(shared,64u*1024*1024);if(remote>=0)close(remote);av_frame_free(&f);avcodec_free_context(&c);close(fd);}
static void dispatch(int fd){uint32_t cmd;if(recv(fd,&cmd,4,MSG_PEEK|MSG_WAITALL)!=4){close(fd);return;}if(cmd){client(fd);return;}
 while(!io(fd,&cmd,4,0)&&cmd==0){int pair[2];if(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,pair))break;pid_t p=fork();if(!p){close(fd);close(pair[0]);client(pair[1]);_exit(0);}close(pair[1]);if(p<0){close(pair[0]);break;}char b=1,anc[CMSG_SPACE(sizeof(int))];memset(anc,0,sizeof anc);struct iovec v={&b,1};struct msghdr m={.msg_iov=&v,.msg_iovlen=1,.msg_control=anc,.msg_controllen=sizeof anc};struct cmsghdr*c=CMSG_FIRSTHDR(&m);c->cmsg_level=SOL_SOCKET;c->cmsg_type=SCM_RIGHTS;c->cmsg_len=CMSG_LEN(sizeof(int));memcpy(CMSG_DATA(c),&pair[0],sizeof(int));int ok=sendmsg(fd,&m,MSG_NOSIGNAL);close(pair[0]);if(ok!=1)break;}close(fd);}
int main(int argc,char**argv){
 if(argc!=2)return 2;signal(SIGPIPE,SIG_IGN);signal(SIGCHLD,SIG_IGN);int fd=-1;
 if(!strcmp(argv[1],"--systemd")){
  const char *pid=getenv("LISTEN_PID"),*fds=getenv("LISTEN_FDS");
  if(!pid||!fds||strtol(pid,NULL,10)!=getpid()||strcmp(fds,"1")){fprintf(stderr,"Missing systemd socket\n");return 2;}
  fd=3;int listening=0;socklen_t size=sizeof(listening);
  if(getsockopt(fd,SOL_SOCKET,SO_ACCEPTCONN,&listening,&size)||!listening)return 2;
 }else{
  fd=socket(AF_UNIX,SOCK_STREAM,0);struct sockaddr_un a={.sun_family=AF_UNIX};
  if(strlen(argv[1])>=sizeof a.sun_path)return 2;strcpy(a.sun_path,argv[1]);umask(0077);
  if(bind(fd,(void*)&a,sizeof a)||listen(fd,8)){perror("bind/listen");return 1;}
 }
 while(1){int s=accept(fd,NULL,NULL);if(s<0){if(errno==EINTR)continue;break;}pid_t p=fork();if(!p){close(fd);dispatch(s);_exit(0);}close(s);}return 1;
}
