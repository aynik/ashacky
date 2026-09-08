// LinuxHost hardware codec service. SPDX-License-Identifier: LGPL-2.1-or-later
import Foundation
import VideoToolbox
import CoreMedia
import CoreVideo
import Darwin
@_silgen_name("lh_release") func lh_release()
let slotSize = 16*1024*1024
let sharedPath = ProcessInfo.processInfo.environment["LH_VIDEO_SHMEM"]!
let sharedFD = open(sharedPath, O_RDWR)
guard sharedFD >= 0 else {fatalError("shared file open failed")}
var sharedStat = stat()
guard fstat(sharedFD, &sharedStat)==0, sharedStat.st_size==64*1024*1024 else {fatalError("shared size")}
let shared = mmap(nil,64*1024*1024,PROT_READ|PROT_WRITE,MAP_SHARED,sharedFD,0)!
guard shared != MAP_FAILED else {fatalError("shared mmap")}
close(sharedFD)
func message(_ s:String) { fputs(s+"\n",stderr) }
func readExact(_ fd:Int32,_ n:Int)->Data? {
    var data=Data(count:n)
    let ok=data.withUnsafeMutableBytes { raw -> Bool in
        var off=0
        while off<n { let r=Darwin.read(fd,raw.baseAddress!.advanced(by:off),n-off);if r<0 && errno==EINTR {continue};if r<=0{return false};off+=r }
        return true
    }
    return ok ? data : nil
}
func writeAll(_ fd:Int32,_ data:Data)->Bool {
    data.withUnsafeBytes { raw in
        var off=0
        while off<data.count {let r=Darwin.write(fd,raw.baseAddress!.advanced(by:off),data.count-off);if r<0 && errno==EINTR {continue};if r<=0{return false};off+=r};return true
    }
}
func words(_ d:Data)->[UInt32] { stride(from:0,to:d.count,by:4).map { i in (0..<4).reduce(UInt32(0)){$0 | UInt32(d[i+$1]) << (8*$1)} } }
func header(_ a:[UInt32])->Data {var d=Data();for var x in a.map({$0.littleEndian}) {withUnsafeBytes(of:&x){d.append(contentsOf:$0)}};return d}
final class Result {var pixel:CVPixelBuffer?;var status:OSStatus=0}
func callback(_ opaque:UnsafeMutableRawPointer?,_ frame:UnsafeMutableRawPointer?,_ status:OSStatus,_ flags:VTDecodeInfoFlags,_ image:CVImageBuffer?,_ pts:CMTime,_ duration:CMTime) {
    guard let frame else{return};let r=Unmanaged<Result>.fromOpaque(frame).takeUnretainedValue();r.pixel=image;r.status=status
}
final class Decoder {
    var session:VTDecompressionSession?
    var format:CMFormatDescription?
    var width=0,height=0,depth=8
    deinit {if let session {VTDecompressionSessionInvalidate(session)}}
    func configure(_ w:Int,_ h:Int,_ config:Data)throws {
        guard session==nil,w>0,h>0,w<=4096,h<=4096,w%2==0,h%2==0,config.count==12 else {throw NSError(domain:"config",code:1)}
        width=w;height=h;depth=Int(config[6]>>4)
        guard (config[4]==0 && depth==8) || (config[4]==2 && depth==10) else {throw NSError(domain:"profile",code:1)}
        VTRegisterSupplementalVideoDecoderIfAvailable(kCMVideoCodecType_VP9)
        guard VTIsHardwareDecodeSupported(kCMVideoCodecType_VP9) else {throw NSError(domain:"no-hardware",code:1)}
        let ext:[CFString:Any]=[kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms:["vpcC":config]]
        var fmt:CMVideoFormatDescription?
        let fs=CMVideoFormatDescriptionCreate(allocator:kCFAllocatorDefault,codecType:kCMVideoCodecType_VP9,width:Int32(w),height:Int32(h),extensions:ext as CFDictionary,formatDescriptionOut:&fmt)
        guard fs==0,let fmt else {throw NSError(domain:"format",code:Int(fs))}
        var cb=VTDecompressionOutputCallbackRecord(decompressionOutputCallback:callback,decompressionOutputRefCon:nil)
        let full=config[6]&1 != 0
        let ps=depth==8 ? (full ? kCVPixelFormatType_420YpCbCr8BiPlanarFullRange:kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange) : (full ? kCVPixelFormatType_420YpCbCr10BiPlanarFullRange:kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange)
        var vs:VTDecompressionSession?
        let st=VTDecompressionSessionCreate(allocator:kCFAllocatorDefault,formatDescription:fmt,decoderSpecification:[kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder:true] as CFDictionary,imageBufferAttributes:[kCVPixelBufferPixelFormatTypeKey:ps] as CFDictionary,outputCallback:&cb,decompressionSessionOut:&vs)
        guard st==0,let vs else {throw NSError(domain:"create",code:Int(st))}
        var selected:CFTypeRef?
        let hs=withUnsafeMutablePointer(to:&selected){VTSessionCopyProperty(vs,key:kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,allocator:kCFAllocatorDefault,valueOut:$0)}
        guard hs==0,(selected as? NSNumber)?.boolValue==true else {VTDecompressionSessionInvalidate(vs);throw NSError(domain:"software-refused",code:1)}
        format=fmt;session=vs;message("codec=vp9 hardware=true width=\(w) height=\(h) depth=\(depth)")
    }
    func decode(_ data:Data,_ serial:UInt32,_ destination:UnsafeMutableRawPointer)throws->Int {
        guard let session,let format else {throw NSError(domain:"unconfigured",code:1)}
        var block:CMBlockBuffer?
        var st=CMBlockBufferCreateWithMemoryBlock(allocator:kCFAllocatorDefault,memoryBlock:nil,blockLength:data.count,blockAllocator:kCFAllocatorDefault,customBlockSource:nil,offsetToData:0,dataLength:data.count,flags:0,blockBufferOut:&block)
        guard st==0,let block else {throw NSError(domain:"block",code:Int(st))}
        st=data.withUnsafeBytes{CMBlockBufferReplaceDataBytes(with:$0.baseAddress!,blockBuffer:block,offsetIntoDestination:0,dataLength:data.count)}
        guard st==0 else {throw NSError(domain:"copy",code:Int(st))}
        var size=data.count;var timing=CMSampleTimingInfo(duration:.invalid,presentationTimeStamp:CMTime(value:Int64(serial),timescale:1000000),decodeTimeStamp:.invalid);var sample:CMSampleBuffer?
        st=CMSampleBufferCreateReady(allocator:kCFAllocatorDefault,dataBuffer:block,formatDescription:format,sampleCount:1,sampleTimingEntryCount:1,sampleTimingArray:&timing,sampleSizeEntryCount:1,sampleSizeArray:&size,sampleBufferOut:&sample)
        guard st==0,let sample else {throw NSError(domain:"sample",code:Int(st))}
        let r=Result()
        st=VTDecompressionSessionDecodeFrame(session,sampleBuffer:sample,flags:[],frameRefcon:Unmanaged.passUnretained(r).toOpaque(),infoFlagsOut:nil)
        VTDecompressionSessionWaitForAsynchronousFrames(session)
        guard st==0,r.status==0 else {throw NSError(domain:"decode",code:Int(st != 0 ? st:r.status))}
        guard let pixel=r.pixel else {return 0}
        guard CVPixelBufferGetWidth(pixel)==width,CVPixelBufferGetHeight(pixel)==height,CVPixelBufferGetPlaneCount(pixel)==2 else {throw NSError(domain:"dimensions",code:1)}
        CVPixelBufferLockBaseAddress(pixel,.readOnly);defer{CVPixelBufferUnlockBaseAddress(pixel,.readOnly)}
        let outputSize=width*height*3/2*(depth==8 ? 1:2);guard outputSize<=slotSize else {throw NSError(domain:"slot-overflow",code:1)};var offset=0
        for plane in 0..<2 {let rows=plane==0 ? height:height/2;let bytes=width*(depth==8 ? 1:2);let stride=CVPixelBufferGetBytesPerRowOfPlane(pixel,plane);guard let p=CVPixelBufferGetBaseAddressOfPlane(pixel,plane),stride>=bytes else {throw NSError(domain:"plane",code:1)}
            for y in 0..<rows {memcpy(destination.advanced(by:offset),p.advanced(by:y*stride),bytes);offset+=bytes} }
        return outputSize
    }
}
func serve(_ fd:Int32,_ slot:Int) {
    defer{close(fd)};var one:Int32=1;setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&one,4);var timeout=timeval(tv_sec:1800,tv_usec:0);setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,socklen_t(MemoryLayout.size(ofValue:timeout)));timeout.tv_sec=5;setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,socklen_t(MemoryLayout.size(ofValue:timeout)))
    let decoder=Decoder();var count=0
    while let h=readExact(fd,16) {let a=words(h);guard a[3]<=16*1024*1024,let payload=readExact(fd,Int(a[3])) else {break}
        do {
            if a[0]==3 {try decoder.configure(Int(a[1]),Int(a[2]),payload);guard writeAll(fd,header([0,0,0,0])) else {break}}
            else if a[0]==2 {
                let bytes=try decoder.decode(payload,a[1],shared.advanced(by:slot*slotSize));count+=1
                lh_release()
                let desc=header([UInt32(slot*slotSize),0,UInt32(bytes),a[1]])
                guard writeAll(fd,header([2,UInt32(decoder.width),UInt32(decoder.height),16])),writeAll(fd,desc) else {break}
            } else {throw NSError(domain:"operation",code:1)}
        } catch {message("codec error: \(error)");_ = writeAll(fd,header([UInt32.max,0,0,0]));break}
    };message("vp9 packets=\(count)")
}
signal(SIGPIPE,SIG_IGN)
let bindIP=ProcessInfo.processInfo.environment["LH_VIDEO_BIND"] ?? "127.0.0.1"
let listener=socket(AF_INET,SOCK_STREAM,0);var reuse:Int32=1;setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&reuse,4)
var addr=sockaddr_in();addr.sin_len=UInt8(MemoryLayout.size(ofValue:addr));addr.sin_family=sa_family_t(AF_INET);addr.sin_port=UInt16(5569).bigEndian;addr.sin_addr.s_addr=inet_addr(bindIP)
let rc=withUnsafePointer(to:&addr){$0.withMemoryRebound(to:sockaddr.self,capacity:1){bind(listener,$0,socklen_t(MemoryLayout.size(ofValue:addr)))}}
guard rc==0,listen(listener,8)==0 else {fatalError("bind/listen failed \(errno)")}
let poolLock=NSLock();var freeSlots=[0,1,2,3];message("LinuxHost shared VP9 service listening on \(bindIP):5569")
while true {let fd=accept(listener,nil,nil);if fd<0 {if errno==EINTR {continue};break};poolLock.lock();let slot=freeSlots.popLast();poolLock.unlock();guard let slot else {close(fd);continue};DispatchQueue.global().async {serve(fd,slot);poolLock.lock();freeSlots.append(slot);poolLock.unlock()}}
