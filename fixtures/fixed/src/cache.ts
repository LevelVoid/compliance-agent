import { createHash } from "node:crypto";

// Etag for HTTP caching. Not a security boundary: the input is a public file
// body and the output is sent to the client anyway.
export function etagFor(fileContents: string): string {
  return createHash("md5").update(fileContents).digest("hex");
}

export function cacheKey(fileContents: string): string {
  return `cache:${etagFor(fileContents)}`;
}
