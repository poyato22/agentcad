// Which tool calls are generative design runs (no imports: the console's log model reads it).
const TOOLS = new Set(["topopt_run", "topopt_generate"]);

/** topopt_run / topopt_generate (bare or MCP-prefixed): their rows carry a result card and
 *  never fold away. */
export const isTopoptCallName = (name: string): boolean => TOOLS.has(name.replace(/^mcp__mises__/, ""));
