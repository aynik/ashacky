#import <Cocoa/Cocoa.h>
#import <MetalKit/MetalKit.h>
#import <AVFoundation/AVFoundation.h>
#include <fcntl.h>
#include <unistd.h>
#import "CSConnection.h"
#import "CSMain.h"
#import "CSInput.h"
#import "CSDisplay+Renderer.h"
#import "CSCursor.h"
#import "CSScreenshot.h"
#import "CSMetalRenderer.h"
#import "keymap.h"
#import "Pasteboard.h"
#import "CSUSBManager.h"
#import "CSUSBManager+Protected.h"
#import <spice-client.h>
#import "CSUSBDevice.h"
#import "CSPort.h"
#import "CSPortDelegate.h"
#import "AshackyHost-Swift.h"

@interface CSUSBManager (LinuxHostFilter)
- (void)setRedirectOnConnectFilter:(NSString *)filter;
@end

@interface HostWindow : NSWindow @end
@implementation HostWindow
- (BOOL)canBecomeKeyWindow { return YES; }
- (BOOL)canBecomeMainWindow { return YES; }
@end
@class App;
@interface GuestView : MTKView
@property(nonatomic,weak) App *app;
@property(nonatomic,strong) CSInput *input;
@property(nonatomic,strong) CSDisplay *display;
@property(nonatomic) CSInputButton buttons;
@property(nonatomic,strong) NSMutableDictionary *touchIDs;
@property(nonatomic,strong) NSArray *touchPoints;
@property(nonatomic) unsigned nextTouchID;
@property(nonatomic) double lastTouch;
@property(nonatomic) unsigned touchButtons;
@property(nonatomic) BOOL nativePointerMode;
- (void)resetTouches;
- (void)sendTouches;
- (BOOL)nativeTouch;
@property(nonatomic,strong) NSMutableSet<NSNumber *> *modifiers;
@property(nonatomic,strong) NSMutableSet<NSNumber *> *pressedKeys;
- (void)releaseGuestKeys;
@end
@interface DisplaySlot : NSObject
@property(nonatomic,strong) HostWindow *window;
@property(nonatomic,strong) GuestView *view;
@property(nonatomic,strong) CSMetalRenderer *renderer;
@property(nonatomic,strong) NSNumber *screenID;
@end
@implementation DisplaySlot @end
@interface App : NSObject<NSApplicationDelegate,CSConnectionDelegate,NSWindowDelegate,CSPortDelegate>
@property(nonatomic,strong) AshackyHostServices *hostServices;
@property(nonatomic) BOOL setupMode;
@property(nonatomic,strong) HostWindow *window;
@property(nonatomic,strong) GuestView *view;
@property(nonatomic,strong) CSMetalRenderer *renderer;
@property(nonatomic,strong) CSConnection *connection;
@property(nonatomic) BOOL borderless;
@property(nonatomic,strong) NSMutableArray<DisplaySlot *> *slots;
@property(nonatomic,strong) NSMutableDictionary<NSNumber *,CSDisplay *> *displays;
@property(nonatomic,strong) CSInput *sharedInput;
- (void)syncScreens;
- (void)configureDisplays;
- (GuestView *)activeView;
@property(nonatomic,strong) CSPort *inputPort;
@property(nonatomic,strong) NSMutableData *inputReply;
@property(nonatomic) double inputReadyUntil;
@property(nonatomic) double inputProbeUntil;
@property(nonatomic) double inputAppliedUntil;
@property(nonatomic,strong) HostPasteboard *pasteboard;
@property(nonatomic) BOOL retryPending;
@property(nonatomic) BOOL hostCursorHidden;
- (void)toggleCapture;

- (void)resizeDisplay;
- (void)updateHostCursor;
- (void)setHostCursorHidden:(BOOL)hidden;
@end
// AppKit normally processes Command keys as menu equivalents and can drop
// their key-up events. Route keyboard events directly only when the VM owns focus.
@interface HostApplication : NSApplication @end
@implementation HostApplication
- (void)sendEvent:(NSEvent *)event {
 NSResponder *responder=self.keyWindow.firstResponder;
 if (self.isActive && [responder isKindOfClass:GuestView.class]) {
  GuestView *view=(GuestView *)responder;
  switch (event.type) {
   case NSEventTypeKeyDown: [view keyDown:event]; return;
   case NSEventTypeKeyUp: [view keyUp:event]; return;
   case NSEventTypeFlagsChanged: [view flagsChanged:event]; return;
   default: break;
  }
 }
 [super sendEvent:event];
}
@end
@implementation GuestView
- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)isFlipped { return YES; }
- (void)updateTrackingAreas {
 [super updateTrackingAreas];
 for (NSTrackingArea *a in self.trackingAreas) [self removeTrackingArea:a];
 [self addTrackingArea:[[NSTrackingArea alloc] initWithRect:NSZeroRect options:NSTrackingMouseMoved|NSTrackingMouseEnteredAndExited|NSTrackingActiveInActiveApp|NSTrackingInVisibleRect owner:self userInfo:nil]];
}
- (void)sendMacKey:(unsigned short)keyCode down:(BOOL)down {
 if (keyCode >= sizeof(mac_to_xt)/sizeof(mac_to_xt[0])) return;
 unsigned code=mac_to_xt[keyCode];
 if ((code&0xff00)==0xe000) code=0x100|(code&255);
 if (code && self.input) {
  if (!self.pressedKeys) self.pressedKeys=[NSMutableSet set];
  if (down) [self.pressedKeys addObject:@(keyCode)]; else [self.pressedKeys removeObject:@(keyCode)];
  [self.input sendKey:down?kCSInputKeyPress:kCSInputKeyRelease code:code];
 }
}
- (void)key:(NSEvent *)e down:(BOOL)down { [self sendMacKey:e.keyCode down:down]; }
- (void)releaseGuestKeys {
 [self resetTouches];
 // Track locally so releases are queued even if SPICE has not processed a press yet.
 for (NSNumber *key in self.pressedKeys.copy) [self sendMacKey:key.unsignedShortValue down:NO];
 [self.modifiers removeAllObjects];
}
- (void)keyDown:(NSEvent *)e {
 NSEventModifierFlags mask=NSEventModifierFlagControl|NSEventModifierFlagOption|NSEventModifierFlagShift;
 if (e.keyCode==111 && (e.modifierFlags&mask)==mask) { [self.app toggleCapture]; return; }
 [self key:e down:YES];
}
- (void)keyUp:(NSEvent *)e { [self key:e down:NO]; }
- (void)flagsChanged:(NSEvent *)e {
 if (!self.modifiers) self.modifiers=[NSMutableSet set];
 NSNumber *code=@(e.keyCode);
 BOOL down=![self.modifiers containsObject:code];
 if (down) [self.modifiers addObject:code]; else [self.modifiers removeObject:code];
 [self key:e down:down];
}
- (void)mouseEntered:(NSEvent *)e { if(![self nativeTouch]) [self.window makeKeyWindow]; [self.app updateHostCursor]; }
- (void)mouseExited:(NSEvent *)e { [self.app setHostCursorHidden:NO]; }
- (void)mouseMoved:(NSEvent *)e {
 [self.app updateHostCursor];
 if ([self nativeTouch]) return;
 if(self.nativePointerMode) { [self.input requestMouseMode:NO];self.nativePointerMode=NO; }
 CGSize size=self.display.displaySize;
 if (size.width<=0 || size.height<=0) return;
 NSPoint p=[self convertPoint:e.locationInWindow fromView:nil];
 CGFloat scale=MIN(self.bounds.size.width/size.width,self.bounds.size.height/size.height);
 CGPoint gp=CGPointMake((p.x-(self.bounds.size.width-size.width*scale)/2)/scale,(p.y-(self.bounds.size.height-size.height*scale)/2)/scale);
 gp.x=MAX(0,MIN(size.width-1,gp.x)); gp.y=MAX(0,MIN(size.height-1,gp.y));
 [self.input sendMousePosition:self.buttons absolutePoint:gp forMonitorID:self.display.monitorID];
 [self.display.cursor moveTo:gp];
}
- (void)mouseDragged:(NSEvent *)e { [self mouseMoved:e]; }
- (void)rightMouseDragged:(NSEvent *)e { [self mouseMoved:e]; }
- (void)otherMouseDragged:(NSEvent *)e { [self mouseMoved:e]; }
- (void)button:(NSEvent *)e down:(BOOL)down {
 if ([self nativeTouch] && e.buttonNumber<3) {
  unsigned bit=1u<<e.buttonNumber;
  if(down) self.touchButtons|=bit; else self.touchButtons&=~bit;
  [self sendTouches]; return;
 }
 [self mouseMoved:e];
 CSInputButton b=e.buttonNumber==0?kCSInputButtonLeft:e.buttonNumber==1?kCSInputButtonRight:e.buttonNumber==2?kCSInputButtonMiddle:e.buttonNumber==3?kCSInputButtonSide:kCSInputButtonExtra;
 if(down) self.buttons|=b; else self.buttons&=~b;
 [self.input sendMouseButton:b mask:self.buttons pressed:down];
}
- (void)mouseDown:(NSEvent *)e { [self button:e down:YES]; }
- (void)mouseUp:(NSEvent *)e { [self button:e down:NO]; }
- (void)rightMouseDown:(NSEvent *)e { [self button:e down:YES]; }
- (void)rightMouseUp:(NSEvent *)e { [self button:e down:NO]; }
- (void)otherMouseDown:(NSEvent *)e { [self button:e down:YES]; }
- (void)otherMouseUp:(NSEvent *)e { [self button:e down:NO]; }
- (BOOL)nativeTouch {
 return self.app.inputAppliedUntil>NSProcessInfo.processInfo.systemUptime && (self.touchPoints.count || self.touchButtons || NSProcessInfo.processInfo.systemUptime-self.lastTouch<0.2);
}
- (void)sendTouches {
 if (!self.app.inputPort.isOpen) return;
 NSData *data=[NSJSONSerialization dataWithJSONObject:@{@"touches":self.touchPoints ?: @[],@"buttons":@(self.touchButtons)} options:0 error:nil];
 NSMutableData *line=[data mutableCopy]; [line appendBytes:"\n" length:1]; [self.app.inputPort writeData:line];
}
- (void)resetTouches { if(self.nativePointerMode) { [self.input requestMouseMode:NO];self.nativePointerMode=NO; } self.touchPoints=@[]; self.touchButtons=0; [self.touchIDs removeAllObjects]; [self sendTouches]; }
- (void)touches:(NSEvent *)event {
 static double logTime=0; double now=NSProcessInfo.processInfo.systemUptime;
 if(now-logTime>2) { NSLog(@"Touch trace active=%d key=%d port=%d contacts=%lu",NSApp.isActive,self.window.isKeyWindow,self.app.inputPort.isOpen,(unsigned long)[event touchesMatchingPhase:NSTouchPhaseTouching inView:self].count);logTime=now; }
 if (!NSApp.isActive || !self.window.isKeyWindow || !self.app.inputPort.isOpen) { [self resetTouches]; return; }
 if (!self.touchIDs) self.touchIDs=[NSMutableDictionary dictionary];
 NSSet *touches=[event touchesMatchingPhase:NSTouchPhaseTouching inView:self];
 NSMutableArray *points=[NSMutableArray array]; NSMutableSet *active=[NSMutableSet set];
 for (NSTouch *touch in touches) {
  if (touch.type!=NSTouchTypeIndirect || points.count>=10) continue;
  NSNumber *ident=self.touchIDs[touch.identity];
  if (!ident) { ident=@(self.nextTouchID++ % 65536);self.touchIDs[touch.identity]=ident; }
  [active addObject:touch.identity]; NSPoint point=touch.normalizedPosition;
  [points addObject:@[ident,@(point.x),@(1-point.y)]];
 }
 for (id key in self.touchIDs.allKeys) if (![active containsObject:key]) [self.touchIDs removeObjectForKey:key];
 if(points.count && self.app.inputAppliedUntil>NSProcessInfo.processInfo.systemUptime && !self.nativePointerMode) { [self.input requestMouseMode:YES];self.nativePointerMode=YES; }
 self.touchPoints=points; self.lastTouch=NSProcessInfo.processInfo.systemUptime; [self sendTouches];
}
- (void)touchesBeganWithEvent:(NSEvent *)e { [self touches:e]; }
- (void)touchesMovedWithEvent:(NSEvent *)e { [self touches:e]; }
- (void)touchesEndedWithEvent:(NSEvent *)e { [self touches:e]; }
- (void)touchesCancelledWithEvent:(NSEvent *)e { [self resetTouches]; }
- (void)scrollWheel:(NSEvent *)e { if ([self nativeTouch]) return; [self.input sendMouseScroll:kCSInputScrollSmooth buttonMask:self.buttons dy:-e.scrollingDeltaY/(e.hasPreciseScrollingDeltas?10:1)]; }
@end
@implementation App
- (void)setHostCursorHidden:(BOOL)hidden {
 if (hidden == self.hostCursorHidden) return;
 _hostCursorHidden=hidden;
 if(hidden) [NSCursor hide]; else [NSCursor unhide];
}
- (GuestView *)activeView {
 for(DisplaySlot *slot in self.slots) if(slot.window.isKeyWindow) return slot.view;
 return self.view;
}
- (void)updateHostCursor {
 GuestView *v=[self activeView];
 NSPoint point=[v convertPoint:v.window.mouseLocationOutsideOfEventStream fromView:nil];
 [self setHostCursorHidden:NSApp.isActive && v.window.isKeyWindow && v.display!=nil && NSPointInRect(point,v.bounds)];
}
- (void)applicationDidResignActive:(NSNotification *)n { [self setHostCursorHidden:NO]; for(DisplaySlot *slot in self.slots) [slot.view releaseGuestKeys]; }
- (void)applicationDidBecomeActive:(NSNotification *)n { [self updateHostCursor]; }
- (void)windowDidBecomeKey:(NSNotification *)n { [self updateHostCursor]; }
- (void)applicationDidFinishLaunching:(NSNotification *)n {
 signal(SIGPIPE,SIG_IGN);
 NSArray<NSString *> *args=NSProcessInfo.processInfo.arguments;
 self.setupMode=[args containsObject:@"--setup"] || !NSProcessInfo.processInfo.environment[@"LINUXHOST_SPICE_SOCKET"];
 NSString *directory=NSProcessInfo.processInfo.environment[@"ASHACKY_PRIVATE_DIRECTORY"];
 NSUInteger setupIndex=[args indexOfObject:@"--setup"];
 if(setupIndex!=NSNotFound && setupIndex+1<args.count) directory=args[setupIndex+1];
 if(!directory) directory=[NSHomeDirectory() stringByAppendingPathComponent:@".ashacky"];
 self.hostServices=[AshackyHostServices new];
 NSError *serviceError=nil;
 if(![self.hostServices startAtDirectory:directory error:&serviceError]) {
  NSLog(@"Ashacky host services failed: %@",serviceError.localizedDescription);
  exit(EXIT_FAILURE);
 }
 if(self.setupMode) { [self.hostServices showPermissions]; return; }
 self.borderless=YES;
 self.slots=[NSMutableArray array];self.displays=[NSMutableDictionary dictionary];
 NSScreen *screen=NSScreen.screens.firstObject;
 for(NSScreen *candidate in NSScreen.screens) if(CGDisplayIsBuiltin([candidate.deviceDescription[@"NSScreenNumber"] unsignedIntValue])) { screen=candidate;break; }
 self.window=[[HostWindow alloc] initWithContentRect:screen.frame styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
 self.window.title=NSProcessInfo.processInfo.environment[@"LINUXHOST_TITLE"] ?: @"Linux";
 self.window.delegate=self;
 self.window.backgroundColor=NSColor.blackColor;
 self.window.acceptsMouseMovedEvents=YES;
 self.window.collectionBehavior=NSWindowCollectionBehaviorManaged;
 self.view=[[GuestView alloc] initWithFrame:NSMakeRect(0,0,screen.frame.size.width,screen.frame.size.height) device:MTLCreateSystemDefaultDevice()];
 self.view.app=self;
 self.view.allowedTouchTypes=NSTouchTypeMaskIndirect;
 self.view.wantsRestingTouches=YES;
 [NSTimer scheduledTimerWithTimeInterval:0.25 repeats:YES block:^(NSTimer *timer) {
  GuestView *active=[self activeView];
  if (self.inputProbeUntil>NSProcessInfo.processInfo.systemUptime || (NSApp.isActive && active.window.isKeyWindow && self.inputReadyUntil>NSProcessInfo.processInfo.systemUptime)) [active sendTouches];
  else if((!NSApp.isActive || !active.window.isKeyWindow) && (active.touchPoints.count || active.touchButtons)) [active resetTouches];
 }];
 self.view.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
 self.view.clearColor=MTLClearColorMake(0,0,0,1);
 self.window.contentView=self.view;
 self.renderer=[[CSMetalRenderer alloc] initWithMetalKitView:self.view];
 self.view.delegate=self.renderer;
 DisplaySlot *primary=[DisplaySlot new];primary.window=self.window;primary.view=self.view;primary.renderer=self.renderer;primary.screenID=screen.deviceDescription[@"NSScreenNumber"];[self.slots addObject:primary];
 [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(syncScreens) name:NSApplicationDidChangeScreenParametersNotification object:nil];
 [self syncScreens];
 NSApp.presentationOptions=NSApplicationPresentationHideDock|NSApplicationPresentationHideMenuBar|NSApplicationPresentationDisableProcessSwitching;
 [self.window makeKeyAndOrderFront:nil]; [self.window makeFirstResponder:self.view]; [NSApp activateIgnoringOtherApps:YES];
 NSLog(@"First window borderless=%d frame=%@ drawable=%@",self.borderless,NSStringFromRect(self.window.frame),NSStringFromSize(self.view.drawableSize));
 [CSMain.sharedInstance spiceSetDebug:NO];
 [CSMain.sharedInstance spiceStart];
 NSString *path=NSProcessInfo.processInfo.environment[@"LINUXHOST_SPICE_SOCKET"];
 if (!path) { NSLog(@"Missing SPICE socket configuration"); [NSApp terminate:nil]; return; }
 self.connection=[[CSConnection alloc] initWithUnixSocketFile:[NSURL fileURLWithPath:path]];
 self.connection.delegate=self;
 self.connection.audioEnabled=YES;
 self.pasteboard=[HostPasteboard new]; self.pasteboard.lastChange=NSPasteboard.generalPasteboard.changeCount;
 self.connection.session.pasteboardDelegate=self.pasteboard;
 [self.connection connect];
 [NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *t) {
  if (NSApp.isActive && self.pasteboard.lastChange != NSPasteboard.generalPasteboard.changeCount) { self.pasteboard.lastChange=NSPasteboard.generalPasteboard.changeCount; [[NSNotificationCenter defaultCenter] postNotificationName:kCSPasteboardChangedNotification object:self.pasteboard]; }
  static CGSize previousSize;
  CGSize currentSize=self.view.display.displaySize;
  if(!CGSizeEqualToSize(previousSize,currentSize)) { previousSize=currentSize; [self resizeDisplay]; }
  NSString *directory=NSProcessInfo.processInfo.environment[@"LINUXHOST_DIAGNOSTICS"];
  NSString *request=[directory stringByAppendingPathComponent:@"spice-capture.request"];
  if (request && [[NSFileManager defaultManager] fileExistsAtPath:request]) {
   [[NSFileManager defaultManager] removeItemAtPath:request error:nil];
   [self.view.display screenshotWithCompletion:^(CSScreenshot *shot) {
    [shot writeToURL:[NSURL fileURLWithPath:[directory stringByAppendingPathComponent:@"spice-display.png"]] atomically:YES];
   }];
  }
 }];
}
- (void)syncScreens {
 if(!self.slots.count)return;
 NSScreen *primary=nil,*secondary=nil;
 for(NSScreen *screen in NSScreen.screens) if([screen.deviceDescription[@"NSScreenNumber"] isEqual:self.slots[0].screenID]) primary=screen;
 if(!primary) primary=NSScreen.screens.firstObject;
 for(NSScreen *screen in NSScreen.screens) if(screen!=primary) { secondary=screen;break; }
 if(self.borderless && primary) [self.window setFrame:primary.frame display:YES];
 if(secondary && self.slots.count==1) {
  DisplaySlot *slot=[DisplaySlot new];slot.screenID=secondary.deviceDescription[@"NSScreenNumber"];
  slot.window=[[HostWindow alloc] initWithContentRect:secondary.frame styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
  slot.window.title=[NSString stringWithFormat:@"%@ — External display",NSProcessInfo.processInfo.environment[@"LINUXHOST_TITLE"] ?: @"Linux"];slot.window.delegate=self;slot.window.backgroundColor=NSColor.blackColor;slot.window.acceptsMouseMovedEvents=YES;slot.window.releasedWhenClosed=NO;
  slot.view=[[GuestView alloc] initWithFrame:NSMakeRect(0,0,secondary.frame.size.width,secondary.frame.size.height) device:MTLCreateSystemDefaultDevice()];
  slot.view.app=self;slot.view.input=self.sharedInput;slot.view.allowedTouchTypes=NSTouchTypeMaskIndirect;slot.view.wantsRestingTouches=YES;slot.view.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;slot.view.clearColor=MTLClearColorMake(0,0,0,1);
  slot.window.contentView=slot.view;slot.renderer=[[CSMetalRenderer alloc] initWithMetalKitView:slot.view];slot.view.delegate=slot.renderer;
  [slot.window makeFirstResponder:slot.view];[self.slots addObject:slot];[slot.window orderFront:nil];
 } else if(!secondary && self.slots.count>1) {
  DisplaySlot *slot=self.slots.lastObject;[slot.view releaseGuestKeys];[slot.view.display removeRenderer:slot.renderer];slot.view.display.isEnabled=NO;slot.window.delegate=nil;[slot.window close];[self.slots removeLastObject];[self.window makeKeyWindow];
 }
 if(secondary && self.slots.count>1) { self.slots[1].screenID=secondary.deviceDescription[@"NSScreenNumber"];[self.slots[1].window setFrame:secondary.frame display:YES]; }
 [self configureDisplays];
 NSLog(@"Host displays=%lu frontend windows=%lu",(unsigned long)NSScreen.screens.count,(unsigned long)self.slots.count);
}
- (void)configureDisplays {
 CGFloat x=0;
 for(NSUInteger i=0;i<self.slots.count;i++) {
  DisplaySlot *slot=self.slots[i];CSDisplay *display=self.displays[@(i)];
  if(display && slot.view.display!=display) { [slot.view.display removeRenderer:slot.renderer];slot.view.display=display;[display addRenderer:slot.renderer]; }
  CGSize size=slot.view.drawableSize;
  if(display && size.width>0 && size.height>0) [display requestResolution:CGRectMake(x,0,size.width,size.height)];
  x+=size.width;
 }
 for(NSNumber *ident in self.displays) if(ident.unsignedIntegerValue>=self.slots.count) self.displays[ident].isEnabled=NO;
 [self resizeDisplay];
}
- (void)retryConnection {
 if(self.retryPending) return;
 self.retryPending=YES;
 dispatch_after(dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC),dispatch_get_main_queue(),^{ self.retryPending=NO; [self.connection connect]; });
}
- (void)toggleCapture {
 [self setHostCursorHidden:NO];
 for(DisplaySlot *slot in self.slots) [slot.view releaseGuestKeys];
 self.borderless=!self.borderless;
 if(self.borderless) {
  self.window.styleMask=NSWindowStyleMaskBorderless;
  [self.window setFrame:self.window.screen.frame display:YES];
  NSApp.presentationOptions=NSApplicationPresentationHideDock|NSApplicationPresentationHideMenuBar|NSApplicationPresentationDisableProcessSwitching;
 } else {
  NSApp.presentationOptions=NSApplicationPresentationDefault;
  self.window.styleMask=NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskResizable|NSWindowStyleMaskMiniaturizable;
  [self.window setFrame:NSMakeRect(120,100,1100,720) display:YES];
 }
 for(NSUInteger i=1;i<self.slots.count;i++) { if(self.borderless) [self.slots[i].window orderFront:nil];else [self.slots[i].window orderOut:nil]; }
 [self.window makeFirstResponder:self.view]; [self resizeDisplay];
}
- (void)resizeDisplay {
 for(DisplaySlot *slot in self.slots) { CGSize size=slot.view.display.displaySize,draw=slot.view.drawableSize;
 if(size.width>0 && size.height>0) slot.renderer.viewportScale=MIN(draw.width/size.width,draw.height/size.height);
 }
}
- (void)observeValueForKeyPath:(NSString *)keyPath ofObject:(id)object change:(NSDictionary *)change context:(void *)context {
 dispatch_async(dispatch_get_main_queue(), ^{ [self resizeDisplay]; });
}
- (void)windowDidResize:(NSNotification *)n { [self resizeDisplay]; }
- (void)windowDidResignKey:(NSNotification *)n { [self setHostCursorHidden:NO]; for(DisplaySlot *slot in self.slots) if(slot.window==n.object) [slot.view releaseGuestKeys]; }
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)s { return YES; }
- (void)applicationWillTerminate:(NSNotification *)n { [self setHostCursorHidden:NO]; [self.connection disconnect]; [self.hostServices stop]; }
- (void)spiceConnected:(CSConnection *)c { dispatch_async(dispatch_get_main_queue(), ^{ NSLog(@"SPICE connected");
 c.usbManager.isAutoConnect=NO;
 }); }
- (void)spiceDisconnected:(CSConnection *)c { dispatch_async(dispatch_get_main_queue(), ^{ [self setHostCursorHidden:NO]; NSLog(@"SPICE disconnected"); [self retryConnection]; }); }
- (void)spiceError:(CSConnection *)c code:(CSConnectionError)code message:(NSString *)s { dispatch_async(dispatch_get_main_queue(), ^{ NSLog(@"SPICE error %ld: %@",(long)code,s); [self retryConnection]; }); }
- (void)spiceInputAvailable:(CSConnection *)c input:(CSInput *)i { dispatch_async(dispatch_get_main_queue(), ^{ self.sharedInput=i;for(DisplaySlot *slot in self.slots) slot.view.input=i; [i requestMouseMode:NO]; NSLog(@"SPICE input ready"); }); }
- (void)spiceInputUnavailable:(CSConnection *)c input:(CSInput *)i { dispatch_async(dispatch_get_main_queue(), ^{ self.sharedInput=nil;for(DisplaySlot *slot in self.slots) { [slot.view releaseGuestKeys];slot.view.input=nil; } }); }
- (void)spiceDisplayCreated:(CSConnection *)c display:(CSDisplay *)d { dispatch_async(dispatch_get_main_queue(), ^{
 self.displays[@(d.monitorID)]=d;[self configureDisplays];[self updateHostCursor];NSLog(@"SPICE display ready monitor=%ld",(long)d.monitorID);
}); }
- (void)spiceDisplayUpdated:(CSConnection *)c display:(CSDisplay *)d { dispatch_async(dispatch_get_main_queue(), ^{ [self resizeDisplay]; }); }
- (void)spiceDisplayDestroyed:(CSConnection *)c display:(CSDisplay *)d { dispatch_async(dispatch_get_main_queue(), ^{
 for(DisplaySlot *slot in self.slots) if(slot.view.display==d) { [d removeRenderer:slot.renderer];slot.view.display=nil; }
 if(self.displays[@(d.monitorID)]==d) [self.displays removeObjectForKey:@(d.monitorID)];[self updateHostCursor];
}); }
- (void)spiceAgentConnected:(CSConnection *)c supportingFeatures:(CSConnectionAgentFeature)f { dispatch_async(dispatch_get_main_queue(), ^{NSLog(@"SPICE guest agent ready");[self configureDisplays];}); }
- (void)spiceAgentDisconnected:(CSConnection *)c { dispatch_async(dispatch_get_main_queue(), ^{}); }
- (void)spiceForwardedPortOpened:(CSConnection *)c port:(CSPort *)p { dispatch_async(dispatch_get_main_queue(), ^{
 if ([p.name isEqualToString:@"org.linuxhost.input"]) { NSLog(@"Touch port opened");self.inputPort=p;self.inputReply=[NSMutableData data];p.delegate=self; }
}); }
- (void)port:(CSPort *)port didRecieveData:(NSData *)data { dispatch_async(dispatch_get_main_queue(), ^{
 if(port!=self.inputPort) return;
 [self.inputReply appendData:data];
 NSString *reply=[[NSString alloc] initWithData:self.inputReply encoding:NSUTF8StringEncoding];
 if([reply containsString:@"LH_INPUT_PROBE\n"]) { self.inputProbeUntil=NSProcessInfo.processInfo.systemUptime+5;[self.inputReply setLength:0]; }
 if([reply containsString:@"LH_INPUT_APPLIED\n"]) { self.inputAppliedUntil=NSProcessInfo.processInfo.systemUptime+0.5;[self.inputReply setLength:0]; }
 if([reply containsString:@"LH_INPUT_READY\n"]) { self.inputReadyUntil=NSProcessInfo.processInfo.systemUptime+3;[self.inputReply setLength:0]; }
 if(self.inputReply.length>256) [self.inputReply setLength:0];
}); }
- (void)portDidDisconect:(CSPort *)port { dispatch_async(dispatch_get_main_queue(), ^{if(port!=self.inputPort)return;self.inputReadyUntil=0;self.inputAppliedUntil=0;for(DisplaySlot *slot in self.slots) [slot.view resetTouches];self.inputPort=nil;}); }
- (void)port:(CSPort *)port didError:(NSString *)error { NSLog(@"Input port error: %@",error);[self portDidDisconect:port]; }
- (void)spiceForwardedPortClosed:(CSConnection *)c port:(CSPort *)p { dispatch_async(dispatch_get_main_queue(), ^{}); }
@end
int main(int argc,const char **argv) {
 const char *logPath=getenv("ASHACKY_DISPLAY_LOG");
 if(logPath) {
  int fd=open(logPath,O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW,0600);
  if(fd<0) return EXIT_FAILURE;
  dup2(fd,STDOUT_FILENO);dup2(fd,STDERR_FILENO);close(fd);
 }
 @autoreleasepool { NSApplication *app=HostApplication.sharedApplication; [app setActivationPolicy:NSApplicationActivationPolicyRegular]; App *delegate=[App new]; app.delegate=delegate; [app run]; }
 return 0;
}
