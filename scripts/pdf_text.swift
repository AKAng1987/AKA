// Print the text of a PDF (macOS PDFKit). Used by ph_index_watch.py for PSE circulars; the Mac has no pdftotext or pypdf.
import PDFKit
import Foundation
if CommandLine.arguments.count > 1, let d = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) { print(d.string ?? "") }
