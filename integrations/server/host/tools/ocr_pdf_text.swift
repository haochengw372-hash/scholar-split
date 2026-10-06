import AppKit
import Foundation
import PDFKit
import Vision

guard CommandLine.arguments.count == 2 else {
    fputs("usage: ocr_pdf_text PDF\n", stderr)
    exit(2)
}

let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let document = PDFDocument(url: url), document.pageCount > 0 else {
    fputs("could not open PDF\n", stderr)
    exit(3)
}

func recognize(_ image: CGImage) throws -> [VNRecognizedTextObservation] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    try VNImageRequestHandler(cgImage: image).perform([request])
    return (request.results ?? []).sorted {
        let ay = $0.boundingBox.midY
        let by = $1.boundingBox.midY
        if abs(ay - by) > 0.015 { return ay > by }
        return $0.boundingBox.minX < $1.boundingBox.minX
    }
}

for index in 0..<document.pageCount {
    autoreleasepool {
        guard let page = document.page(at: index) else { return }
        let bounds = page.bounds(for: .mediaBox)
        let maxDimension: CGFloat = 2600
        let scale = min(4.0, maxDimension / max(bounds.width, bounds.height))
        let size = NSSize(width: max(1, bounds.width * scale), height: max(1, bounds.height * scale))
        let thumbnail = page.thumbnail(of: size, for: .mediaBox)
        guard let data = thumbnail.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: data),
              let cgImage = bitmap.cgImage else { return }
        do {
            let observations = try recognize(cgImage)
            print("[PAGE \(index + 1)]")
            for observation in observations {
                if let candidate = observation.topCandidates(1).first {
                    print(candidate.string)
                }
            }
            print("")
        } catch {
            fputs("OCR failed on page \(index + 1): \(error)\n", stderr)
        }
    }
}
