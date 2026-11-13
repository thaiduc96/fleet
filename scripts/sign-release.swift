// Sign a release zip with the Fleet release key and check it against the public key the app embeds.
//   swift scripts/sign-release.swift <zip> <private-key-file> <expected-public-key>
// Prints the base64 signature (the content of <zip>.sig) on stdout.
import CryptoKit
import Foundation

let args = CommandLine.arguments
guard args.count == 4 else {
    FileHandle.standardError.write(Data("usage: sign-release.swift <zip> <key-file> <public-key>\n".utf8))
    exit(2)
}
let zip = try Data(contentsOf: URL(fileURLWithPath: args[1]))
let keyText = try String(contentsOfFile: args[2], encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
guard let raw = Data(base64Encoded: keyText), let key = try? Curve25519.Signing.PrivateKey(rawRepresentation: raw) else {
    FileHandle.standardError.write(Data("bad private key\n".utf8))
    exit(1)
}
guard key.publicKey.rawRepresentation.base64EncodedString() == args[3] else {
    FileHandle.standardError.write(Data("this key does not match the public key embedded in the app\n".utf8))
    exit(1)
}
let signature = try key.signature(for: zip)
guard key.publicKey.isValidSignature(signature, for: zip) else { exit(1) }
print(signature.base64EncodedString())
