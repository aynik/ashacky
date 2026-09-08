// SPDX-License-Identifier: MIT
#define PAM_SM_AUTH
#include <security/pam_modules.h>
#include <security/pam_ext.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <json-c/json.h>
PAM_EXTERN int pam_sm_authenticate(pam_handle_t *pamh,int flags,int argc,const char **argv){
 const char *user=NULL;const void *item=NULL;const char *mapped=NULL,*purpose;int fd,result=PAM_AUTHINFO_UNAVAIL;char response[16384];size_t n=0;
 for(int i=0;i<argc;i++)if(!strncmp(argv[i],"user=",5))mapped=argv[i]+5;
 if(!mapped||pam_get_user(pamh,&user,NULL)!=PAM_SUCCESS||!user||strcmp(user,mapped))return PAM_IGNORE;
 if(pam_get_item(pamh,PAM_SERVICE,&item)!=PAM_SUCCESS||!item)return PAM_IGNORE;
 if(!strcmp(item,"sudo")||!strcmp(item,"polkit-1"))purpose="sudo";
 else if(!strcmp(item,"gdm-password"))purpose="unlock";
 else return PAM_IGNORE;
 if(pam_info(pamh,"Authenticate with Touch ID on your Mac. Cancel to use your Linux password.")!=PAM_SUCCESS)return PAM_CONV_ERR;
 fd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);if(fd<0)return result;
 struct timeval timeout={85,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
 struct sockaddr_un address={.sun_family=AF_UNIX};strcpy(address.sun_path,"/run/linuxhost/agent.sock");
 if(connect(fd,(void*)&address,sizeof(address)))goto end;
 char request[128];int length=snprintf(request,sizeof(request),"{\"action\":\"authenticate\",\"purpose\":\"%s\"}\n",purpose);
 if(send(fd,request,length,MSG_NOSIGNAL)!=length)goto end;
 while(n<sizeof(response)-1){ssize_t got=recv(fd,response+n,1,0);if(got!=1)goto end;if(response[n++]=='\n')break;}
 if(!n||response[n-1]!='\n')goto end;
 response[n]=0;
 struct json_object *value=json_tokener_parse(response),*ok=NULL;
 if(value&&json_object_is_type(value,json_type_object)&&json_object_object_get_ex(value,"ok",&ok)&&json_object_is_type(ok,json_type_boolean)&&json_object_get_boolean(ok))result=PAM_SUCCESS;
 if(value)json_object_put(value);
 end:close(fd);return result;
}
PAM_EXTERN int pam_sm_setcred(pam_handle_t *pamh,int flags,int argc,const char **argv){return PAM_SUCCESS;}
