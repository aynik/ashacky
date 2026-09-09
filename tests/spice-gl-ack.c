// SPDX-License-Identifier: MIT
// Isolated socket-backpressure fixture; no VM, renderer or authentication.
#include "spice-client.h"
#include "spice-channel-priv.h"
#include <sys/socket.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <signal.h>
static void empty_draw(SpiceMarshaller *m, const SpiceMsgcDisplayGlDrawDone *data) {(void)m;(void)data;}
static void log_handler(const gchar *domain,GLogLevelFlags level,const gchar *message,gpointer data) {
 (void)domain;(void)level;(void)data;
 fprintf(stderr,"fixture GL acknowledgement: %s\n",message);
 if (strstr(message,"wait_id")) _exit(42);
}
int main(int argc,char **argv) {
 int fd[2];g_assert_cmpint(socketpair(AF_UNIX,SOCK_STREAM,0,fd),==,0);
 fcntl(fd[0],F_SETFL,O_NONBLOCK);fcntl(fd[1],F_SETFL,O_NONBLOCK);
 SpiceSession *session=spice_session_new();
 SpiceChannel *channel=spice_channel_new(session,SPICE_CHANNEL_DISPLAY,0);
 static SpiceMessageMarshallers marshallers;
 marshallers.msgc_display_gl_draw_done=empty_draw;
 channel->priv->marshallers=&marshallers;channel->priv->use_mini_header=TRUE;
 GError *error=NULL;GSocket *sock=g_socket_new_from_fd(fd[0],&error);g_assert_no_error(error);
 channel->priv->sock=g_object_ref(sock);
 GSocketConnection *connection=g_socket_connection_factory_create_connection(sock);
 channel->priv->out=g_object_ref(g_io_stream_get_output_stream(G_IO_STREAM(connection)));
 if(argc>1 && strcmp(argv[1],"blocked")==0) {
  char buffer[4096]={0};while(send(fd[0],buffer,sizeof(buffer),0)>0){}
  g_assert(errno==EAGAIN || errno==EWOULDBLOCK);
  // Simulate the normal display coroutine already suspended awaiting input.
  channel->priv->coroutine.wait_id=1;
 }
 g_log_set_handler("GSpice",G_LOG_LEVEL_CRITICAL|G_LOG_LEVEL_WARNING,log_handler,NULL);
 alarm(3);
 spice_display_channel_gl_draw_done(SPICE_DISPLAY_CHANNEL(channel));
 unsigned queued=channel->priv->xmit_queue.length;
 printf("GL acknowledgement returned; queued=%u\n",queued);fflush(stdout);
 // Process is intentionally a disposable transport fixture; no running main
 // loop/coroutine, host services or display is involved.
 _exit(queued==1?0:1);
}
