#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

// Run on macOS after building the frontend; creates no window or VM connection.
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) { fprintf(stderr, "Usage: metal-shader <renderer.bundle>\n"); return 2; }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) { fprintf(stderr, "No Metal device available\n"); return 1; }
        NSError *error = nil;
        NSBundle *bundle = [NSBundle bundleWithPath:[NSString stringWithUTF8String:argv[1]]];
        id<MTLLibrary> library = [device newDefaultLibraryWithBundle:bundle error:&error];
        MTLRenderPipelineDescriptor *descriptor = [MTLRenderPipelineDescriptor new];
        descriptor.vertexFunction = [library newFunctionWithName:@"vertexShader"];
        descriptor.fragmentFunction = [library newFunctionWithName:@"samplingShader"];
        descriptor.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
        if (!descriptor.vertexFunction || !descriptor.fragmentFunction) {
            fprintf(stderr, "Cannot load CocoaSpice shader functions: %s\n", error.description.UTF8String);
            return 1;
        }
        id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:descriptor error:&error];
        if (!pipeline) { fprintf(stderr, "Pipeline failed: %s\n", error.description.UTF8String); return 1; }
        printf("CocoaSpice Metal shader and pipeline verified on %s\n", device.name.UTF8String);
    }
    return 0;
}
