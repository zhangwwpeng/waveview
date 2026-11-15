import { existsSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

/**
 * Display-only renderer for assistant replies in Oh My Pi (`omp`).
 *
 * Top-level ```` ```python_wave ```` / ```` ```waveview ```` fences are
 * executed via the installed `waveview` CLI (`waveview render - -o -`,
 * which locates `d` or the last `Diagram`, ignores stray stdout, and skips
 * `if __name__ == "__main__":`); stdout is sliced from `<svg` to `</svg>`
 * and rewritten into a ```svg fence, which the TUI rasterizes inline.
 *
 * Nested fences inside outer 4+-backtick or tilde blocks are ignored per
 * CommonMark rules. Stored replies, `/export`, and model context keep the
 * original fence; only display output changes. Results are cached per block,
 * and blocks are left untouched while streaming.
 */

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
const WAVE_CACHE = new Map<string, string>();
const OPEN_FENCE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

export function renderWaveBlock(code: string): string {
	const hit = WAVE_CACHE.get(code);
	if (hit !== undefined) return hit;

	const cmd = resolveWaveviewCmd(REPO_ROOT);
	let out: string;
	if (!cmd) {
		out =
			"```text\nwaveview 渲染失败：未找到 waveview CLI，请先安装到 ~/.local/bin（例如 uv tool install git+https://github.com/zhangwwpeng/waveview）\n```\n\n```python\n" +
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

		const fence = match[1]!;
		const fenceChar = fence[0]!;
		const fenceLen = fence.length;
		const info = match[2]!.trim();
		if (fenceChar === "`" && info.includes("`")) {
			out.push(rawLine);
			i++;
			continue;
		}

		let closeIdx = -1;
		const closeRe = new RegExp(`^ {0,3}${fenceChar}{${fenceLen},}[ \\t]*$`);
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
		if (fenceChar === "`" && (head === "python_wave" || head === "waveview")) {
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
		if (streaming) return undefined;
		if (!text.includes("```python_wave") && !text.includes("```waveview")) return undefined;
		const replaced = replaceWaveFences(text);
		return replaced === text ? undefined : replaced;
	});
}
