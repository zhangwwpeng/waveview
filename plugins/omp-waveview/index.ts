import { existsSync, readFileSync, realpathSync, statSync, unlinkSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

/**
 * Display-only renderers for assistant replies in Oh My Pi (`omp`).
 *
 * 1. ```python_wave / ```waveview
 *    Executed via the installed `waveview` CLI (`waveview render - -o -`,
 *    which locates `d` or the last `Diagram`, ignores stray stdout, and skips
 *    `if __name__ == "__main__":`); stdout is sliced from `<svg` to `</svg>`
 *    and rewritten into a ```svg fence, which the TUI rasterizes inline.
 *
 * 2. ```img / ```image
 *    Embeds a local image file as a data-URI `<svg>` in the transcript
 *    (downscaling large images to JPEG first so repaints stay lightweight).
 *
 * The stored reply, `/export`, and model context keep the original fence;
 * only display output changes. Results are cached per block, and blocks are
 * left untouched while streaming.
 */

function detectRepoRoot(): string | undefined {
	const fromEnv = process.env.WAVEVIEW_ROOT?.trim();
	if (fromEnv && existsSync(fromEnv)) return fromEnv;
	if (!import.meta.dirname) return undefined;
	try {
		const pluginDir = realpathSync(import.meta.dirname);
		const candidate = join(pluginDir, "..", "..");
		if (existsSync(join(candidate, "src", "waveview", "__init__.py"))) {
			return candidate;
		}
	} catch {
		return undefined;
	}
	return undefined;
}

function resolveWaveviewCmd(repoRoot: string | undefined): [string, ...string[]] | undefined {
	const renderArgs = ["render", "-", "-o", "-"];
	const fromEnv = process.env.WAVEVIEW_BIN?.trim();
	if (fromEnv) return [fromEnv, ...renderArgs];

	const onPath = Bun.which("waveview");
	if (onPath) return [onPath, ...renderArgs];

	const localBin = join(homedir(), ".local", "bin", process.platform === "win32" ? "waveview.exe" : "waveview");
	if (existsSync(localBin)) return [localBin, ...renderArgs];

	if (repoRoot) {
		const binDir = process.platform === "win32" ? "Scripts" : "bin";
		const venvWaveview = join(repoRoot, ".venv", binDir, process.platform === "win32" ? "waveview.exe" : "waveview");
		if (existsSync(venvWaveview)) return [venvWaveview, ...renderArgs];

		const venvPy = join(repoRoot, ".venv", binDir, process.platform === "win32" ? "python.exe" : "python");
		if (existsSync(venvPy)) return [venvPy, "-m", "waveview", ...renderArgs];

		return ["uv", "run", "--project", repoRoot, "waveview", ...renderArgs];
	}
	return undefined;
}

const REPO_ROOT = detectRepoRoot();
const TIMEOUT_MS = 30_000;
const WAVE_FENCE = /```(?:python_wave|waveview)[^\n]*\n([\s\S]*?)```/g;
const IMG_FENCE = /```(?:img|image)[^\n]*\n([\s\S]*?)```/g;
const MAX_WIDTH = 1000;
const MAX_HEIGHT = 600;
const MAX_EMBED_BYTES = 120_000;
const WAVE_CACHE = new Map<string, string>();
const IMG_CACHE = new Map<string, string>();

export function renderWaveBlock(code: string): string {
	const hit = WAVE_CACHE.get(code);
	if (hit !== undefined) return hit;

	const cmd = resolveWaveviewCmd(REPO_ROOT);
	let out: string;
	if (!cmd) {
		out =
			"```text\nwaveview 渲染失败：未找到 waveview CLI，请先安装到 ~/.local/bin（例如 uv tool install -e <repo> 或 uv tool install git+...）\n```\n\n```python\n" +
			code +
			"\n```";
		WAVE_CACHE.set(code, out);
		return out;
	}

	const proc = Bun.spawnSync({
		cmd,
		cwd: process.cwd(),
		stdin: Buffer.from(code),
		stdout: "pipe",
		stderr: "pipe",
		timeout: TIMEOUT_MS,
	});

	const stdout = proc.stdout.toString();
	const stderr = proc.stderr.toString();
	const start = stdout.indexOf("<svg");
	const end = start >= 0 ? stdout.indexOf("</svg>", start) : -1;

	if (proc.exitCode === 0 && start >= 0 && end > start) {
		out = "```svg\n" + stdout.slice(start, end + 6) + "\n```";
	} else {
		const why = (stderr.trim() || stdout.trim() || `exit ${proc.exitCode}`).split("\n").slice(-6).join("\n");
		out = "```text\nwaveview 渲染失败：" + why + "\n```\n\n```python\n" + code + "\n```";
	}
	WAVE_CACHE.set(code, out);
	return out;
}

function resolveLocal(name: string): string | undefined {
	const sessions = join(homedir(), ".omp", "agent", "sessions");
	const proc = Bun.spawnSync({
		cmd: ["find", sessions, "-name", name, "-type", "f"],
		stdout: "pipe",
		stderr: "ignore",
	});
	const hits = proc.stdout
		.toString()
		.split("\n")
		.map((line) => line.trim())
		.filter(Boolean);
	if (hits.length === 0) return undefined;
	hits.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
	return hits[0];
}

function resolvePath(raw: string): string | undefined {
	const value = raw.replace(/^["'`]+|["'`]+$/g, "").trim();
	if (!value) return undefined;
	if (value.startsWith("local://")) return resolveLocal(value.slice("local://".length));
	if (value.startsWith("~/")) return join(homedir(), value.slice(2));
	if (value.startsWith("/")) return value;
	const cwdPath = join(process.cwd(), value);
	if (!existsSync(cwdPath) && REPO_ROOT) {
		const repoPath = join(REPO_ROOT, value);
		if (existsSync(repoPath)) return repoPath;
	}
	return cwdPath;
}

function mimeFor(path: string): string {
	const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
	if (ext === "jpg" || ext === "jpeg") return "image/jpeg";
	if (ext === "gif") return "image/gif";
	if (ext === "webp") return "image/webp";
	if (ext === "svg") return "image/svg+xml";
	return "image/png";
}

function imageSize(buf: Buffer): [number, number] {
	if (buf.length > 24 && buf[0] === 0x89 && buf[1] === 0x50) return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
	if (buf.length > 10 && buf.toString("ascii", 0, 3) === "GIF") return [buf.readUInt16LE(6), buf.readUInt16LE(8)];
	if (buf.length > 4 && buf[0] === 0xff && buf[1] === 0xd8) {
		let i = 2;
		while (i + 9 < buf.length) {
			if (buf[i] !== 0xff) {
				i++;
				continue;
			}
			const marker = buf[i + 1]!;
			if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
				return [buf.readUInt16BE(i + 7), buf.readUInt16BE(i + 5)];
			}
			i += 2 + buf.readUInt16BE(i + 2);
		}
	}
	return [800, 600];
}

/** Re-encode an oversized image to a small JPEG so the data URI stays cheap. */
function shrink(path: string): { path: string; mime: string } | undefined {
	const out = join(tmpdir(), `waveview-embed-${statSync(path).mtimeMs}-${process.pid}.jpg`);
	const proc = Bun.spawnSync({
		cmd: ["sips", "-Z", String(MAX_WIDTH), "-s", "format", "jpeg", "-s", "formatOptions", "60", path, "--out", out],
		stdout: "ignore",
		stderr: "ignore",
	});
	if (proc.exitCode !== 0) return undefined;
	try {
		return { path: out, mime: "image/jpeg" };
	} catch {
		return undefined;
	}
}

export function renderImageBlock(body: string): string {
	const raw = body.trim().split("\n")[0]!.trim();
	const path = resolvePath(raw);
	if (!path) return "```text\n找不到图片：" + raw + "\n```";

	let buf: Buffer;
	try {
		buf = readFileSync(path);
	} catch {
		return "```text\n找不到图片：" + raw + "\n```";
	}
	if (buf.length === 0) return "```text\n图片是空的：" + path + "\n```";

	const key = `${path}:${buf.length}`;
	const hit = IMG_CACHE.get(key);
	if (hit !== undefined) return hit;

	let mime = mimeFor(path);
	let [width, height] = imageSize(buf);
	if (buf.length > MAX_EMBED_BYTES || width > MAX_WIDTH) {
		const small = shrink(path);
		if (small) {
			try {
				buf = readFileSync(small.path);
				mime = small.mime;
				[width, height] = imageSize(buf);
			} finally {
				try {
					unlinkSync(small.path);
				} catch {}
			}
		}
	}

	const scale = Math.min(1, MAX_WIDTH / width, MAX_HEIGHT / height);
	const w = Math.max(1, Math.round(width * scale));
	const h = Math.max(1, Math.round(height * scale));
	const svg =
		`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">` +
		`<image href="data:${mime};base64,${buf.toString("base64")}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet"/>` +
		`</svg>`;

	const out = "```svg\n" + svg + "\n```";
	IMG_CACHE.set(key, out);
	return out;
}

export default function waveview(pi: ExtensionAPI): void {
	pi.registerAssistantTextRenderer(({ text, streaming }) => {
		if (streaming) return undefined;
		const hasWave = text.includes("```python_wave") || text.includes("```waveview");
		const hasImg = text.includes("```img") || text.includes("```image");
		if (!hasWave && !hasImg) return undefined;
		let out = text;
		if (hasWave) out = out.replace(WAVE_FENCE, (_match, code) => renderWaveBlock(code));
		if (hasImg) out = out.replace(IMG_FENCE, (_match, body) => renderImageBlock(body));
		return out;
	});
}
