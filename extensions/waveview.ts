import { spawnSync } from "node:child_process";
import { accessSync, constants, existsSync, realpathSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { delimiter, join } from "node:path";

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

/**
 * Display-only renderer for assistant replies in Oh My Pi (`omp`).
 *
 * Top-level ```` ```python_wave ```` / ```` ```waveview ```` / tilde fences are
 * executed via the installed `waveview` CLI (`waveview render - -o -`,
 * which locates `d` or the last `Diagram`, ignores stray stdout, and skips
 * `if __name__ == "__main__":`); stdout is sliced from `<svg` to `</svg>`
 * and rewritten into a ```svg fence, which the TUI rasterizes inline.
 *
 * Execution runs in `os.tmpdir()` with an allowlisted environment and a bounded
 * timeout, and can be disabled anytime with `WAVEVIEW_DISABLE=1`. Nested fences
 * inside outer 4+-backtick/tilde blocks are ignored per CommonMark rules.
 */

const REPO_ROOT = detectRepoRoot();
const DEFAULT_TIMEOUT_MS = 8_000;
const FAILURE_TTL_MS = 5_000;
const MAX_CACHE_ENTRIES = 256;
const WAVE_CACHE = new Map<string, string>();
const FAILURE_CACHE = new Map<string, { out: string; expiresAt: number }>();
const OPEN_FENCE_RE = /^([ \t]*)(`{3,}|~{3,})(.*)$/;

const ENV_ALLOWLIST = [
	"PATH",
	"HOME",
	"USERPROFILE",
	"LANG",
	"LC_ALL",
	"LC_CTYPE",
	"TMPDIR",
	"TEMP",
	"TMP",
	"SystemRoot",
	"WINDIR",
	"VIRTUAL_ENV",
	"UV_CACHE_DIR",
	"UV_PYTHON_INSTALL_DIR",
	"XDG_CACHE_HOME",
	"XDG_DATA_HOME",
	"SSL_CERT_FILE",
	"REQUESTS_CA_BUNDLE",
] as const;

export function isWaveviewDisabled(): boolean {
	const raw = process.env.WAVEVIEW_DISABLE?.trim().toLowerCase();
	return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export function clearWaveCache(): void {
	WAVE_CACHE.clear();
	FAILURE_CACHE.clear();
}

export function dedentWaveCode(code: string): string {
	const lines = code.split(/\r?\n/);
	let minIndent: number | undefined;
	for (const line of lines) {
		if (!line.trim()) continue;
		const leading = /^([ \t]*)/.exec(line)?.[1]?.length ?? 0;
		minIndent = minIndent === undefined ? leading : Math.min(minIndent, leading);
	}
	if (!minIndent) return code;
	let prefix: string | undefined;
	for (const line of lines) {
		if (!line.trim()) continue;
		const head = line.slice(0, minIndent);
		if (prefix === undefined) {
			prefix = head;
		} else if (prefix !== head) {
			return code;
		}
	}
	if (!prefix) return code;
	return lines
		.map((line) => (line.trim() ? line.slice(prefix.length) : ""))
		.join("\n");
}

function detectRepoRoot(): string | undefined {
	const fromEnv = process.env.WAVEVIEW_ROOT?.trim();
	if (fromEnv && existsSync(fromEnv)) return fromEnv;
	if (!import.meta.dirname) return undefined;
	try {
		const pluginDir = realpathSync(import.meta.dirname);
		for (const candidate of [pluginDir, join(pluginDir, ".."), join(pluginDir, "..", "..")]) {
			if (existsSync(join(candidate, "src", "waveview", "__init__.py"))) {
				return candidate;
			}
		}
	} catch {
		return undefined;
	}
	return undefined;
}

function whichCmd(cmd: string): string | undefined {
	const bunWhich = (globalThis as { Bun?: { which?: (c: string) => string | null } }).Bun?.which;
	if (typeof bunWhich === "function") {
		const hit = bunWhich(cmd);
		if (hit) return hit;
	}
	const pathVal = process.env.PATH ?? "";
	const exts =
		process.platform === "win32"
			? (process.env.PATHEXT ?? ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean)
			: [""];
	for (const dir of pathVal.split(delimiter)) {
		if (!dir) continue;
		for (const ext of exts) {
			const full = join(dir, cmd + ext);
			try {
				accessSync(full, process.platform === "win32" ? constants.F_OK : constants.X_OK);
				return full;
			} catch {
				// try next entry
			}
		}
	}
	return undefined;
}

function resolveWaveviewCmd(repoRoot: string | undefined): [string, ...string[]] | undefined {
	const renderArgs = ["render", "-", "-o", "-"];
	const fromEnv = process.env.WAVEVIEW_BIN?.trim();
	if (fromEnv) return [fromEnv, ...renderArgs];

	const onPath = whichCmd("waveview");
	if (onPath) return [onPath, ...renderArgs];

	const localBin = join(homedir(), ".local", "bin", process.platform === "win32" ? "waveview.exe" : "waveview");
	if (existsSync(localBin)) return [localBin, ...renderArgs];

	if (repoRoot) {
		const binDir = process.platform === "win32" ? "Scripts" : "bin";
		const venvWaveview = join(repoRoot, ".venv", binDir, process.platform === "win32" ? "waveview.exe" : "waveview");
		if (existsSync(venvWaveview)) return [venvWaveview, ...renderArgs];

		const venvPy = join(repoRoot, ".venv", binDir, process.platform === "win32" ? "python.exe" : "python");
		if (existsSync(venvPy)) return [venvPy, "-m", "waveview", ...renderArgs];

		if (whichCmd("uv")) return ["uv", "run", "--project", repoRoot, "waveview", ...renderArgs];
	}
	return undefined;
}

function childEnv(): NodeJS.ProcessEnv {
	const env: NodeJS.ProcessEnv = {
		PYTHONUTF8: "1",
		PYTHONIOENCODING: "utf-8",
		PYTHONDONTWRITEBYTECODE: "1",
	};
	for (const key of ENV_ALLOWLIST) {
		const val = process.env[key];
		if (val !== undefined) env[key] = val;
	}
	return env;
}

function resolveTimeoutMs(): number {
	const raw = process.env.WAVEVIEW_TIMEOUT_MS?.trim();
	if (raw) {
		const parsed = Number(raw);
		if (Number.isFinite(parsed) && parsed > 0) return parsed;
	}
	return DEFAULT_TIMEOUT_MS;
}

function cacheSuccess(code: string, svgFence: string): void {
	if (WAVE_CACHE.size >= MAX_CACHE_ENTRIES) {
		const oldest = WAVE_CACHE.keys().next().value;
		if (oldest !== undefined) WAVE_CACHE.delete(oldest);
	}
	WAVE_CACHE.set(code, svgFence);
}

function failureBlock(why: string, code: string): string {
	return "```text\nwaveview 渲染失败：" + why + "\n```\n\n```python\n" + code + "\n```";
}

export function renderWaveBlock(rawCode: string): string {
	const code = dedentWaveCode(rawCode);
	const hit = WAVE_CACHE.get(code);
	if (hit !== undefined) return hit;

	const cmd = resolveWaveviewCmd(REPO_ROOT);
	const failKey = (cmd ? cmd.join("\0") : "missing") + "\0" + code;
	const failHit = FAILURE_CACHE.get(failKey);
	if (failHit && Date.now() < failHit.expiresAt) return failHit.out;

	if (!cmd) {
		const out = failureBlock(
			"未找到 waveview CLI，请先安装到 ~/.local/bin（例如 uv tool install git+https://github.com/zhangwwpeng/waveview）",
			code,
		);
		FAILURE_CACHE.set(failKey, { out, expiresAt: Date.now() + FAILURE_TTL_MS });
		return out;
	}

	const timeout = resolveTimeoutMs();
	const proc = spawnSync(cmd[0], cmd.slice(1), {
		cwd: tmpdir(),
		env: childEnv(),
		input: code,
		encoding: "utf-8",
		timeout,
		maxBuffer: 16 * 1024 * 1024,
	});

	const stdout = proc.stdout ?? "";
	const stderr = proc.stderr ?? "";
	const start = stdout.indexOf("<svg");
	const end = start >= 0 ? stdout.indexOf("</svg>", start) : -1;

	if (!proc.error && proc.status === 0 && start >= 0 && end > start) {
		const out = "```svg\n" + stdout.slice(start, end + 6) + "\n```";
		cacheSuccess(code, out);
		return out;
	}

	let why: string;
	const errCode = (proc.error as NodeJS.ErrnoException | undefined)?.code;
	if (errCode === "ETIMEDOUT") {
		why = `执行超时（>${timeout}ms），请检查脚本是否有死循环`;
	} else if (errCode === "ENOENT") {
		why = `未找到可执行文件：${cmd[0]}`;
	} else if (proc.error) {
		why = proc.error.message;
	} else {
		const fallback = proc.signal ? `killed by signal ${proc.signal}` : `exit ${proc.status ?? "unknown"}`;
		why = (stderr.trim() || stdout.trim() || fallback).split("\n").slice(-6).join("\n");
	}
	const out = failureBlock(why, code);
	FAILURE_CACHE.set(failKey, { out, expiresAt: Date.now() + FAILURE_TTL_MS });
	return out;
}

function splitLinesKeepEnds(text: string): string[] {
	const lines: string[] = [];
	let start = 0;
	for (let i = 0; i < text.length; i++) {
		if (text.charCodeAt(i) === 10) {
			lines.push(text.slice(start, i + 1));
			start = i + 1;
		}
	}
	if (start < text.length) lines.push(text.slice(start));
	return lines;
}

export function replaceWaveFences(text: string): string {
	if (isWaveviewDisabled()) return text;
	const lines = splitLinesKeepEnds(text);
	const out: string[] = [];
	let i = 0;

	while (i < lines.length) {
		const rawLine = lines[i]!;
		const cleanLine = rawLine.replace(/\r?\n$/, "");
		const match = OPEN_FENCE_RE.exec(cleanLine);
		if (!match) {
			out.push(rawLine);
			i++;
			continue;
		}

		const fence = match[2]!;
		const fenceChar = fence[0]!;
		const fenceLen = fence.length;
		const info = match[3]!.trim();
		if (fenceChar === "`" && info.includes("`")) {
			out.push(rawLine);
			i++;
			continue;
		}

		const escChar = fenceChar === "`" ? "`" : "~";
		const closeRe = new RegExp(`^[ \\t]*${escChar}{${fenceLen},}[ \\t]*$`);
		let closeIdx = -1;
		for (let j = i + 1; j < lines.length; j++) {
			const candidate = lines[j]!.replace(/\r?\n$/, "");
			if (closeRe.test(candidate)) {
				closeIdx = j;
				break;
			}
		}

		if (closeIdx < 0) {
			out.push(rawLine);
			i++;
			continue;
		}

		const head = info.split(/\s+/)[0] ?? "";
		if (head === "python_wave" || head === "waveview") {
			const body = lines.slice(i + 1, closeIdx).join("");
			const nlMatch = /\r?\n$/.exec(lines[closeIdx]!);
			out.push(renderWaveBlock(body) + (nlMatch ? nlMatch[0] : ""));
		} else {
			for (let j = i; j <= closeIdx; j++) {
				out.push(lines[j]!);
			}
		}
		i = closeIdx + 1;
	}

	return out.join("");
}

export default function waveview(pi: ExtensionAPI): void {
	pi.registerAssistantTextRenderer(({ text, streaming }) => {
		if (streaming || isWaveviewDisabled()) return undefined;
		if (!text.includes("python_wave") && !text.includes("waveview")) return undefined;
		const replaced = replaceWaveFences(text);
		return replaced === text ? undefined : replaced;
	});
}
