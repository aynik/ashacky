/* LinuxHost shared VirGL video planes. SPDX-License-Identifier: LGPL-2.1-or-later */
#define EGL_EGLEXT_PROTOTYPES
#include "graphics.h"
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <GLES2/gl2ext.h>
#include <gbm.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
struct LHGraphics {struct gbm_device*g;EGLDisplay d;EGLContext c;};
struct LHFrame {struct gbm_bo*b[2];EGLImage image[2];GLuint t[2];unsigned w,h;};
struct Saved {EGLDisplay d;EGLContext c;EGLSurface rd,wr;EGLenum api;};
static struct Saved enter(struct LHGraphics*g){struct Saved s={eglGetCurrentDisplay(),eglGetCurrentContext(),eglGetCurrentSurface(EGL_READ),eglGetCurrentSurface(EGL_DRAW),eglQueryAPI()};eglBindAPI(EGL_OPENGL_API);eglMakeCurrent(g->d,EGL_NO_SURFACE,EGL_NO_SURFACE,g->c);return s;}
static void leave(struct LHGraphics*g,struct Saved s){eglMakeCurrent(g->d,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);eglBindAPI(s.api);if(s.d!=EGL_NO_DISPLAY)eglMakeCurrent(s.d,s.wr,s.rd,s.c);}
struct LHGraphics*lh_graphics_new(int fd){struct LHGraphics*g=calloc(1,sizeof*g);if(!g)return NULL;g->g=gbm_create_device(fd);if(!g->g)goto fail;g->d=eglGetPlatformDisplay(EGL_PLATFORM_GBM_KHR,g->g,NULL);if(!eglInitialize(g->d,NULL,NULL))goto fail;EGLenum old=eglQueryAPI();eglBindAPI(EGL_OPENGL_API);g->c=eglCreateContext(g->d,NULL,EGL_NO_CONTEXT,(EGLint[]){EGL_CONTEXT_MAJOR_VERSION,2,EGL_CONTEXT_MINOR_VERSION,1,EGL_NONE});eglBindAPI(old);if(g->c==EGL_NO_CONTEXT)goto fail;return g;
fail:lh_graphics_free(g);return NULL;}
void lh_graphics_free(struct LHGraphics*g){if(!g)return;if(g->c)eglDestroyContext(g->d,g->c);if(g->d)eglTerminate(g->d);if(g->g)gbm_device_destroy(g->g);free(g);}
void lh_frame_free(struct LHGraphics*g,struct LHFrame*f){if(!f)return;struct Saved s=enter(g);for(int i=0;i<2;i++){if(f->t[i])glDeleteTextures(1,&f->t[i]);if(f->image[i])eglDestroyImage(g->d,f->image[i]);if(f->b[i])gbm_bo_destroy(f->b[i]);}leave(g,s);free(f);}
int lh_frame_export(struct LHGraphics*g,struct LHFrame**fp,unsigned w,unsigned h,unsigned bytes,const unsigned char*p,VADRMPRIMESurfaceDescriptor*out){
 if(!g)return -1;struct Saved s=enter(g);struct LHFrame*f=*fp;int err=-1;
 if(!f){f=calloc(1,sizeof*f);if(!f)goto done;f->w=w;f->h=h;*fp=f;for(int i=0;i<2;i++){unsigned pw=i?w/2:w,ph=i?h/2:h,format=bytes==2?(i?GBM_FORMAT_GR1616:GBM_FORMAT_R16):(i?GBM_FORMAT_GR88:GBM_FORMAT_R8);f->b[i]=gbm_bo_create(g->g,pw,ph,format,GBM_BO_USE_RENDERING|GBM_BO_USE_LINEAR);if(!f->b[i])goto done;int fd=gbm_bo_get_fd(f->b[i]);if(fd<0)goto done;EGLAttrib a[]={EGL_WIDTH,pw,EGL_HEIGHT,ph,EGL_LINUX_DRM_FOURCC_EXT,format,EGL_DMA_BUF_PLANE0_FD_EXT,fd,EGL_DMA_BUF_PLANE0_OFFSET_EXT,0,EGL_DMA_BUF_PLANE0_PITCH_EXT,gbm_bo_get_stride(f->b[i]),EGL_NONE};f->image[i]=eglCreateImage(g->d,EGL_NO_CONTEXT,EGL_LINUX_DMA_BUF_EXT,NULL,a);close(fd);if(!f->image[i])goto done;glGenTextures(1,&f->t[i]);glBindTexture(GL_TEXTURE_2D,f->t[i]);((PFNGLEGLIMAGETARGETTEXTURE2DOESPROC)eglGetProcAddress("glEGLImageTargetTexture2DOES"))(GL_TEXTURE_2D,f->image[i]);}}
 glPixelStorei(GL_UNPACK_ALIGNMENT,1);for(int i=0;i<2;i++){glBindTexture(GL_TEXTURE_2D,f->t[i]);glTexSubImage2D(GL_TEXTURE_2D,0,0,0,i?w/2:w,i?h/2:h,i?GL_RG:GL_RED,bytes==2?GL_UNSIGNED_SHORT:GL_UNSIGNED_BYTE,p+(i?w*h*bytes:0));}glFinish();if(glGetError()!=GL_NO_ERROR)goto done;
 memset(out,0,sizeof*out);out->fourcc=bytes==2?VA_FOURCC_P010:VA_FOURCC_NV12;out->width=w;out->height=h;out->num_objects=2;out->num_layers=2;
 for(int i=0;i<2;i++){out->objects[i].fd=gbm_bo_get_fd(f->b[i]);if(out->objects[i].fd<0){if(i)close(out->objects[0].fd);goto done;}out->objects[i].size=gbm_bo_get_stride(f->b[i])*(i?h/2:h);out->objects[i].drm_format_modifier=gbm_bo_get_modifier(f->b[i]);out->layers[i].drm_format=bytes==2?(i?GBM_FORMAT_GR1616:GBM_FORMAT_R16):(i?GBM_FORMAT_GR88:GBM_FORMAT_R8);out->layers[i].num_planes=1;out->layers[i].object_index[0]=i;out->layers[i].pitch[0]=gbm_bo_get_stride(f->b[i]);}err=0;
done:leave(g,s);if(err&&*fp){lh_frame_free(g,*fp);*fp=NULL;}return err;}
