#!/usr/bin/env python3
"""PUT real local bytes to an existing LibTV upload_ticket; never creates tickets."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


def upload(ticket, source, part_size=None):
    if ticket.get("originAllowed") is not True:
        raise ValueError("upload origin is not admitted")
    parts = ticket.get("parts", [])
    if not parts or [p.get("partNumber") for p in parts] != list(range(1, len(parts) + 1)):
        raise ValueError("ticket needs ordered numbered parts")
    size = source.stat().st_size
    if not size:
        raise ValueError("empty source")
    if len(parts) > 1:
        if not isinstance(part_size, int) or part_size <= 0:
            raise ValueError("multipart upload needs the exact part_size used for upload_ticket")
        if (size + part_size - 1) // part_size != len(parts):
            raise ValueError("part_size does not match ticket/source")
    else:
        part_size = size
    results, digest = [], hashlib.sha256()
    with source.open("rb") as stream:
        for part in parts:
            url = urllib.parse.urlsplit(part["url"])
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or part.get("method") != "PUT"):
                raise ValueError("ticket part must be a credential-free signed HTTPS PUT")
            body = stream.read(part_size)
            digest.update(body)
            # urllib otherwise injects form content-type and invalidates the OSS signature.
            headers = {"Content-Type": "", **part.get("headers", {})}
            request = urllib.request.Request(part["url"], data=body, method="PUT", headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                if response.status != 200:
                    raise ValueError("part PUT did not return 200")
                results.append({"partNumber": part["partNumber"], "bytes": len(body),
                                "etag": response.headers.get("ETag")})
        if stream.read(1):
            raise ValueError("source was not completely uploaded")
    return {"ok": True, "uploadId": ticket["uploadId"], "path": ticket["path"],
            "bytes": size, "sha256": digest.hexdigest(), "parts": results,
            "next": "Use the SAME uploadId/path with upload_complete, then upload_media with its returned file_ref."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ticket")
    p.add_argument("--file", required=True)
    p.add_argument("--part-size", type=int)
    p.add_argument("--receipt", required=True)
    args = p.parse_args()
    try:
        receipt = Path(args.receipt)
        if receipt.exists():
            raise ValueError("preserve prior PUT receipt; recover with upload_complete instead of reuploading")
        result = upload(json.loads(Path(args.ticket).read_text()), Path(args.file), args.part_size)
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except Exception as exc:
        result = {"ok": False, "error": str(exc),
                  "recovery": "Preserve this ticket. Inspect the original upload before retrying; do not automatically create another ticket."}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
