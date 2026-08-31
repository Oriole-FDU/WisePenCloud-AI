import { spawn } from "node:child_process";
import { writeFile } from "node:fs/promises";

const serverPath = "C:/Users/12732/AppData/Local/next-ai-drawio-mcp/runtime/node_modules/@next-ai-drawio/mcp-server/dist/index.js";
const resultPath = "C:/Users/12732/AppData/Local/next-ai-drawio-mcp/drawio-session-result.json";

const xml = String.raw`<mxfile host="app.diagrams.net" modified="2026-08-30T00:00:00.000Z" agent="next-ai-drawio" version="24.7.17">
  <diagram id="rag-competition" name="RAG Architecture">
    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="850" pageHeight="650" math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>

        <mxCell id="title" value="Agent 可编排的 RAG 工具链" style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=22;fontStyle=1;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="20" y="10" width="760" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="subtitle" value="检索要精确，阅读要完整；每一步都能被 Agent 独立调用、穿插与复用" style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=12;fontColor=#526777;" vertex="1" parent="1">
          <mxGeometry x="20" y="38" width="760" height="22" as="geometry"/>
        </mxCell>

        <mxCell id="panel1" value="" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#EAF6FD;strokeColor=#4A9BCB;strokeWidth=1.5;arcSize=8;" vertex="1" parent="1">
          <mxGeometry x="20" y="75" width="240" height="225" as="geometry"/>
        </mxCell>
        <mxCell id="panel2" value="" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF8E1;strokeColor=#D6B24C;strokeWidth=1.5;arcSize=8;" vertex="1" parent="1">
          <mxGeometry x="280" y="75" width="240" height="225" as="geometry"/>
        </mxCell>
        <mxCell id="panel3" value="" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF0E8;strokeColor=#E78047;strokeWidth=1.5;arcSize=8;" vertex="1" parent="1">
          <mxGeometry x="540" y="75" width="240" height="225" as="geometry"/>
        </mxCell>

        <mxCell id="p1h" value="① 文档检索与阅读" style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=16;fontStyle=1;fontColor=#2176A5;" vertex="1" parent="1">
          <mxGeometry x="30" y="88" width="220" height="26" as="geometry"/>
        </mxCell>
        <mxCell id="p1a" value="子块切分 + Dense / BM25" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#4A9BCB;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="45" y="125" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p1b" value="混合召回：精确命中" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#D9F0FC;strokeColor=#4A9BCB;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="45" y="171" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p1c" value="Section 边界 + 标题树邻域" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#4A9BCB;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="45" y="217" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p1d" value="动态父块恢复：完整语境" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#D9F0FC;strokeColor=#4A9BCB;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="45" y="263" width="190" height="26" as="geometry"/>
        </mxCell>

        <mxCell id="p2h" value="② 图谱检索与推理" style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=16;fontStyle=1;fontColor=#A47C16;" vertex="1" parent="1">
          <mxGeometry x="290" y="88" width="220" height="26" as="geometry"/>
        </mxCell>
        <mxCell id="p2a" value="实体发现：从原文抽取种子" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#D6B24C;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="305" y="125" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p2b" value="图谱 High / Low 检索" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF4C7;strokeColor=#D6B24C;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="305" y="171" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p2c" value="Neo4j 邻域扩展：多跳关系" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#D6B24C;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="305" y="217" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p2d" value="关系路径 = 推理线索" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF4C7;strokeColor=#D6B24C;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="305" y="263" width="190" height="26" as="geometry"/>
        </mxCell>

        <mxCell id="p3h" value="③ 证据核验与组装" style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=16;fontStyle=1;fontColor=#B45E2D;" vertex="1" parent="1">
          <mxGeometry x="550" y="88" width="220" height="26" as="geometry"/>
        </mxCell>
        <mxCell id="p3a" value="反查原文：回到 Section / Chunk" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#E78047;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="565" y="125" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p3b" value="证据核验：事实与路径对齐" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFE2D1;strokeColor=#E78047;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="565" y="171" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p3c" value="Contextualize：补齐背景信息" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#E78047;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="565" y="217" width="190" height="34" as="geometry"/>
        </mxCell>
        <mxCell id="p3d" value="可回溯：页码 / 标题路径 / 图谱路径" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFE2D1;strokeColor=#E78047;fontSize=11;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="565" y="263" width="190" height="26" as="geometry"/>
        </mxCell>

        <mxCell id="flowPanel" value="" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#6B7D8A;strokeWidth=1.5;arcSize=8;" vertex="1" parent="1">
          <mxGeometry x="20" y="330" width="760" height="245" as="geometry"/>
        </mxCell>
        <mxCell id="flowTitle" value="核心闭环：混合检索驱动多跳推理" style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=17;fontStyle=1;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="40" y="343" width="720" height="28" as="geometry"/>
        </mxCell>
        <mxCell id="q" value="用户问题" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#EAF6FD;strokeColor=#4A9BCB;fontSize=13;fontStyle=1;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="42" y="405" width="98" height="42" as="geometry"/>
        </mxCell>
        <mxCell id="hybrid" value="文档混合检索&lt;br&gt;Dense + BM25" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#D9F0FC;strokeColor=#4A9BCB;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="164" y="397" width="130" height="58" as="geometry"/>
        </mxCell>
        <mxCell id="entity" value="发现实体&lt;br&gt;作为图谱种子" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF4C7;strokeColor=#D6B24C;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="318" y="397" width="130" height="58" as="geometry"/>
        </mxCell>
        <mxCell id="multi" value="Neo4j 多跳扩展&lt;br&gt;沿关系路径推理" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF4C7;strokeColor=#D6B24C;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="472" y="397" width="130" height="58" as="geometry"/>
        </mxCell>
        <mxCell id="evidence" value="反查原文 + 核验证据&lt;br&gt;生成可追溯上下文" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFE2D1;strokeColor=#E78047;fontSize=12;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="626" y="397" width="130" height="58" as="geometry"/>
        </mxCell>
        <mxCell id="agent" value="Agent 决策：回答 / 调用下一工具 / 再次检索" style="rounded=1;whiteSpace=wrap;html=1;fillColor=#F4F7F9;strokeColor=#6B7D8A;fontSize=12;fontStyle=1;fontColor=#243746;" vertex="1" parent="1">
          <mxGeometry x="205" y="505" width="390" height="42" as="geometry"/>
        </mxCell>

        <mxCell id="e1" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#4A9BCB;strokeWidth=1.5;endArrow=block;endFill=1;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="q" target="hybrid"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e2" value="发现实体" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#D6B24C;strokeWidth=1.5;endArrow=block;endFill=1;exitX=1;exitY=0.5;entryX=0;entryY=0.5;fontSize=10;fontColor=#A47C16;" edge="1" parent="1" source="hybrid" target="entity"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e3" value="种子" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#D6B24C;strokeWidth=1.5;endArrow=block;endFill=1;exitX=1;exitY=0.5;entryX=0;entryY=0.5;fontSize=10;fontColor=#A47C16;" edge="1" parent="1" source="entity" target="multi"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e4" value="关系路径" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#E78047;strokeWidth=1.5;endArrow=block;endFill=1;exitX=1;exitY=0.5;entryX=0;entryY=0.5;fontSize=10;fontColor=#B45E2D;" edge="1" parent="1" source="multi" target="evidence"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e5" value="回到 Agent" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#6B7D8A;strokeWidth=1.5;endArrow=block;endFill=1;exitX=0.5;exitY=1;entryX=1;entryY=0.5;fontSize=10;fontColor=#526777;" edge="1" parent="1" source="evidence" target="agent"><mxGeometry relative="1" as="geometry"/></mxCell>
        <mxCell id="e6" value="按需再进入 RAG" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;dashed=1;strokeColor=#526777;strokeWidth=1.3;endArrow=block;endFill=1;exitX=0;exitY=0.5;entryX=0.5;entryY=1;fontSize=10;fontColor=#526777;" edge="1" parent="1" source="agent" target="hybrid"><mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="100" y="570"/><mxPoint x="100" y="475"/><mxPoint x="229" y="475"/></Array></mxGeometry></mxCell>

        <mxCell id="legend" value="蓝：文档精确召回　 黄：图谱关系推理　 橙：证据核验与上下文组装" style="text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;fontSize=11;fontColor=#526777;" vertex="1" parent="1">
          <mxGeometry x="40" y="584" width="720" height="20" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>`;

const child = spawn(process.execPath, [serverPath], {
  cwd: "C:/Users/12732/AppData/Local/next-ai-drawio-mcp/runtime",
  env: { ...process.env, PORT: "6002" },
  stdio: ["pipe", "pipe", "pipe"],
});

let buffer = "";
let nextId = 1;
const pending = new Map();
const events = [];

child.stdout.on("data", (chunk) => {
  buffer += chunk.toString();
  const lines = buffer.split(/\r?\n/);
  buffer = lines.pop() ?? "";
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const message = JSON.parse(line);
      if (message.id && pending.has(message.id)) {
        pending.get(message.id)(message);
        pending.delete(message.id);
      } else {
        events.push(message);
      }
    } catch (error) {
      events.push({ parseError: String(error), line });
    }
  }
});
child.stderr.on("data", (chunk) => events.push({ stderr: chunk.toString() }));
child.on("exit", (code, signal) => events.push({ exit: { code, signal } }));

function request(method, params, timeoutMs = 30000) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`Timed out waiting for ${method}`));
    }, timeoutMs);
    pending.set(id, (message) => {
      clearTimeout(timer);
      resolve(message);
    });
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
  });
}

try {
  const initialize = await request("initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "codex-local-client", version: "1.0.0" },
  });
  child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} })}\n`);
  const started = await request("tools/call", { name: "start_session", arguments: {} });
  const created = await request("tools/call", { name: "create_new_diagram", arguments: { xml } }, 60000);
  const listed = await request("tools/call", { name: "list_pages", arguments: {} });
  const result = { initialize, started, created, listed, events, pid: child.pid, resultPath };
  await writeFile(resultPath, JSON.stringify(result, null, 2), "utf8");
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  const result = { error: String(error), events, pid: child.pid };
  await writeFile(resultPath, JSON.stringify(result, null, 2), "utf8");
  console.error(JSON.stringify(result, null, 2));
  process.exitCode = 1;
}

// Keep the MCP HTTP bridge alive for the browser session.
setInterval(() => {}, 60_000);
