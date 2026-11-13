import Foundation
import Testing
@testable import FleetCore

// ---------------------------------------------------------------- InviteCode
@Test func inviteCodeRoundTrips() throws {
    let code = InviteCode(url: "https://home.tail.ts.net", token: String(repeating: "ab12", count: 12))

    let decoded = try InviteCode.decode(code.encoded())

    #expect(decoded == code)
}

@Test func inviteCodeIsURLSafeAndPrefixed() {
    let text = InviteCode(url: "https://h.ts.net", token: String(repeating: "f", count: 48)).encoded()

    #expect(text.hasPrefix("fleet1.") && !text.contains("+") && !text.contains("/") && !text.contains("="))
}

@Test func inviteCodeToleratesSurroundingWhitespaceAndTrailingSlash() throws {
    let raw = "fleet1." + Base64URL.encode(Data(#"{"url":"https://h.ts.net/","token":"abcdef0123456789abcd"}"#.utf8))

    let decoded = try InviteCode.decode("  \(raw)\n")

    #expect(decoded == InviteCode(url: "https://h.ts.net", token: "abcdef0123456789abcd"))
}

@Test(arguments: [
    ("hello", InviteCode.DecodeError.wrongPrefix),
    ("fleet1.%%%", .notBase64),
    ("fleet1." + Base64URL.encode(Data("not json".utf8)), .badPayload),
    ("fleet1." + Base64URL.encode(Data(#"{"url":"file:///etc","token":"abcdef0123456789abcd"}"#.utf8)), .badURL),
    ("fleet1." + Base64URL.encode(Data(#"{"url":"https://h.ts.net","token":"short"}"#.utf8)), .badToken),
    ("fleet1." + Base64URL.encode(Data(#"{"url":"https://h.ts.net","token":"has spaces in it ok?"}"#.utf8)), .badToken),
])
func inviteCodeRejectsBadInput(text: String, expected: InviteCode.DecodeError) {
    #expect(throws: expected) { try InviteCode.decode(text) }
}

// ---------------------------------------------------------------- Version
@Test(arguments: [
    ("1.0.0", "1.0.1", true), ("1.9.0", "1.10.0", true), ("v2.0.0", "1.99.99", false),
    ("1.0.0-dev", "1.0.0", true), ("1.0.0", "1.0.0-dev", false), ("1.2.3", "1.2.3", false),
])
func versionOrdering(a: String, b: String, aIsOlder: Bool) throws {
    let va = try #require(Version(a))
    let vb = try #require(Version(b))

    #expect((va < vb) == aIsOlder)
}

@Test(arguments: ["", "1.0", "1.0.0.0", "a.b.c", "-1.0.0"])
func versionRejectsGarbage(text: String) {
    #expect(Version(text) == nil)
}

// ---------------------------------------------------------------- Release
private let releaseJSON = Data("""
{"tag_name":"v1.2.0","draft":false,"prerelease":false,"assets":[
 {"name":"Fleet-1.2.0.zip","browser_download_url":"https://github.com/o/r/releases/download/v1.2.0/Fleet-1.2.0.zip"},
 {"name":"checksums.txt","browser_download_url":"https://github.com/o/r/releases/download/v1.2.0/checksums.txt"},
 {"name":"Fleet-1.2.0.zip.sig","browser_download_url":"https://github.com/o/r/releases/download/v1.2.0/Fleet-1.2.0.zip.sig"},
 {"name":"install.sh","browser_download_url":"https://github.com/o/r/releases/download/v1.2.0/install.sh"}]}
""".utf8)

@Test func releaseParsesTagAndAssets() throws {
    let r = try Release.parse(releaseJSON)

    #expect(r.version.description == "1.2.0" && r.zipURL.lastPathComponent == "Fleet-1.2.0.zip"
            && r.checksumsURL.lastPathComponent == "checksums.txt")
}

@Test func releaseWithoutZipIsAnError() {
    let data = Data(#"{"tag_name":"v1.2.0","assets":[]}"#.utf8)

    #expect(throws: Release.ParseError.missingAsset("Fleet-1.2.0.zip")) { try Release.parse(data) }
}

@Test func releaseWithoutSignatureIsAnError() {
    let data = Data("""
    {"tag_name":"v1.2.0","assets":[
     {"name":"Fleet-1.2.0.zip","browser_download_url":"https://x/Fleet-1.2.0.zip"},
     {"name":"checksums.txt","browser_download_url":"https://x/checksums.txt"}]}
    """.utf8)

    #expect(throws: Release.ParseError.missingAsset("Fleet-1.2.0.zip.sig")) { try Release.parse(data) }
}

// ---------------------------------------------------------------- UpdateSignature
@Test func signatureVerifiesWithTheMatchingPublicKey() throws {
    let keys = UpdateSignature.generate()
    let zip = Data("release bytes".utf8)

    let sig = try UpdateSignature.sign(zip, privateKeyBase64: keys.privateKey)

    #expect(UpdateSignature.verify(zip, signatureBase64: sig, publicKeyBase64: keys.publicKey))
}

@Test func signatureFailsForTamperedData() throws {
    let keys = UpdateSignature.generate()
    let sig = try UpdateSignature.sign(Data("original".utf8), privateKeyBase64: keys.privateKey)

    #expect(!UpdateSignature.verify(Data("tampered".utf8), signatureBase64: sig, publicKeyBase64: keys.publicKey))
}

@Test func signatureFailsForAnotherKey() throws {
    let signer = UpdateSignature.generate()
    let other = UpdateSignature.generate()
    let zip = Data("bytes".utf8)
    let sig = try UpdateSignature.sign(zip, privateKeyBase64: signer.privateKey)

    #expect(!UpdateSignature.verify(zip, signatureBase64: sig, publicKeyBase64: other.publicKey))
}

@Test(arguments: ["", "not base64!", "AAAA"])
func garbageSignatureIsRejected(sig: String) {
    let keys = UpdateSignature.generate()

    #expect(!UpdateSignature.verify(Data("x".utf8), signatureBase64: sig, publicKeyBase64: keys.publicKey))
}

@Test func publicKeyIsDerivedFromPrivateKey() throws {
    let keys = UpdateSignature.generate()

    #expect(try UpdateSignature.publicKey(ofPrivate: keys.privateKey) == keys.publicKey)
}

@Test func checksumIsFoundByFileName() {
    let hex = String(repeating: "a1", count: 32)
    let text = "\(String(repeating: "0", count: 64))  install.sh\n\(hex)  Fleet-1.2.0.zip\n"

    #expect(Release.checksum(for: "Fleet-1.2.0.zip", in: text) == hex)
}

@Test func checksumRejectsMalformedHash() {
    #expect(Release.checksum(for: "Fleet-1.2.0.zip", in: "xyz  Fleet-1.2.0.zip\n") == nil)
}

// ---------------------------------------------------------------- FleetSummary
private func summary(_ json: String) throws -> FleetSummary {
    try JSONDecoder().decode(FleetSummary.self, from: Data(json.utf8))
}

@Test func summaryCountsOnlyOnlineMachines() throws {
    let s = try summary("""
    {"machines":[
     {"machine":"home","label":"Nhà","online":true,"isSelf":true,"sessions":[
        {"id":"a","state":"needs_input","name":"fix"},{"id":"b","state":"working"}]},
     {"machine":"work","label":"Cty","online":false,"sessions":[{"id":"c","state":"needs_input"}]}]}
    """)

    #expect(s.needsInput.map(\.id) == ["home/a"] && s.working.map(\.id) == ["home/b"])
}

@Test func summaryDecodesOldFleetdWithoutNewFields() throws {
    let s = try summary(#"{"machines":[{"machine":"work","sessions":[{"id":"x"}]}]}"#)

    #expect(s.machines.first?.label == "work" && s.machines.first?.sessions.first?.state == "unknown")
}

@Test func transitionsReportOnlyChangesIntoInterestingStates() throws {
    let s = try summary("""
    {"machines":[{"machine":"home","online":true,"sessions":[
      {"id":"a","state":"needs_input"},{"id":"b","state":"working"},{"id":"c","state":"done"}]}]}
    """)
    let before = ["home/a": "working", "home/b": "idle", "home/c": "done"]

    let changed = s.transitions(from: before, into: ["needs_input", "done", "failed"])

    #expect(changed.map(\.id) == ["home/a"])
}
