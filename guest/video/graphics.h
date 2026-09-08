/* SPDX-License-Identifier: LGPL-2.1-or-later */
#pragma once
#include <va/va_drmcommon.h>
struct LHGraphics;
struct LHFrame;
struct LHGraphics* lh_graphics_new(int fd);
void lh_graphics_free(struct LHGraphics*);
void lh_frame_free(struct LHGraphics*,struct LHFrame*);
int lh_frame_export(struct LHGraphics*,struct LHFrame**,unsigned,unsigned,unsigned,const unsigned char*,VADRMPRIMESurfaceDescriptor*);
