#import "CSSession.h"
@interface HostPasteboard : NSObject<CSPasteboardDelegate>
@property NSInteger lastChange;
@end
@implementation HostPasteboard
- (NSPasteboardType)type:(CSPasteboardType)t {
 switch(t) {case kCSPasteboardTypeString:return NSPasteboardTypeString;case kCSPasteboardTypePng:return NSPasteboardTypePNG;case kCSPasteboardTypeTiff:return NSPasteboardTypeTIFF;case kCSPasteboardTypeHtml:return NSPasteboardTypeHTML;default:return nil;}
}
- (BOOL)canReadItemForType:(CSPasteboardType)t { NSString *type=[self type:t];return type && [NSPasteboard.generalPasteboard.types containsObject:type]; }
- (NSData *)dataForType:(CSPasteboardType)t { NSString *type=[self type:t];return type?[NSPasteboard.generalPasteboard dataForType:type]:nil; }
- (NSString *)string {return [NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];}
- (void)clearContents {[NSPasteboard.generalPasteboard clearContents];self.lastChange=NSPasteboard.generalPasteboard.changeCount;}
- (void)setData:(NSData *)data forType:(CSPasteboardType)t {NSString *type=[self type:t];if(type){[self clearContents];[NSPasteboard.generalPasteboard setData:data forType:type];self.lastChange=NSPasteboard.generalPasteboard.changeCount;}}
- (void)setString:(NSString *)s {[self clearContents];[NSPasteboard.generalPasteboard setString:s forType:NSPasteboardTypeString];self.lastChange=NSPasteboard.generalPasteboard.changeCount;}
@end
