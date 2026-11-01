#!/usr/bin/env node
// sum-tokens.mjs — total API token usage of one build, read offline from its Claude Code transcripts.
//
// Usage: node sum-tokens.mjs <projects-dir>
//   <projects-dir> is the run's fake $HOME/.claude/projects/; every *.jsonl under it is read,
//   subagent transcripts included.
//
// Prints one JSON object: the four usage fields summed, input_total (uncached + cache writes +
// cache reads, the input the build actually consumed), and the transcript / API-call counts.
//
// Claude Code writes one API response as several lines (one per content block), each repeating the
// same `usage`, so lines are deduplicated by message id + request id before summing; within a group
// the largest value per field wins, which also covers a partially streamed earlier line.

import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const USAGE_FIELDS = [
  "input_tokens",
  "cache_creation_input_tokens",
  "cache_read_input_tokens",
  "output_tokens",
];

export function listTranscripts(dir) {
  if (!existsSync(dir)) return [];
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) found.push(...listTranscripts(path));
    else if (entry.isFile() && entry.name.endsWith(".jsonl")) found.push(path);
  }
  return found.sort();
}

export function sumUsage(files) {
  const calls = new Map();
  for (const file of files) {
    readFileSync(file, "utf8").split("\n").forEach((line, index) => {
      if (!line.trim()) return;
      let record;
      try {
        record = JSON.parse(line);
      } catch {
        return;
      }
      const usage = record?.message?.usage;
      if (!usage || typeof usage !== "object") return;
      const key = record.message.id
        ? `${record.message.id}|${record.requestId ?? ""}`
        : `${file}:${index}`;
      const seen = calls.get(key) ?? Object.fromEntries(USAGE_FIELDS.map((f) => [f, 0]));
      for (const field of USAGE_FIELDS) {
        const value = Number.isFinite(usage[field]) ? usage[field] : 0;
        seen[field] = Math.max(seen[field], value);
      }
      calls.set(key, seen);
    });
  }
  const totals = Object.fromEntries(USAGE_FIELDS.map((f) => [f, 0]));
  for (const call of calls.values()) {
    for (const field of USAGE_FIELDS) totals[field] += call[field];
  }
  return {
    ...totals,
    input_total:
      totals.input_tokens + totals.cache_creation_input_tokens + totals.cache_read_input_tokens,
    transcripts: files.length,
    api_calls: calls.size,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const dir = process.argv[2];
  if (!dir) {
    console.error("usage: node sum-tokens.mjs <projects-dir>");
    process.exit(2);
  }
  console.log(JSON.stringify(sumUsage(listTranscripts(dir))));
}
