/**
 * computer-use-advance
 *
 * 这个包本身不做任何事 —— 它的全部作用是在 cordis.patch.yml 里把本仓库的两个
 * MCP server(computer / krita)挂进 DSH profile, 由官方的
 * @deepseek-ai/dsh-mcp-client 负责拉起 Python 进程并注册工具。
 *
 * 为什么不用 JS 重写工具: 28 个工具的 schema 只维护在 computer_mcp/server.py
 * 一份, 在这里重写会出现"两份真相"并必然漂移。
 *
 * @module computer-use-advance
 */

/** Cordis plugin name used by loader diagnostics. */
export const name = 'computer-use-advance';

/** 本插件不注入任何服务 —— 挂载工作全部由 patch 完成。 */
export const inject = [];

/** Apply the plugin (intentionally empty). */
export function apply() {}
