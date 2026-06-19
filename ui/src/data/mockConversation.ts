import type { ChatAppMessage, ModelOption, RuntimeSettings, WebSearchCredential } from "../types/chat";

export const defaultRuntimeSettings: RuntimeSettings = {
  mode: "backend",
  baseUrl: "/api",
  fromSource: "APISIX-wX0iR6tY",
  userId: "10001",
  identityType: "2",
  modelId: "",
  providerId: "",
  searchProvider: "4get_ddg",
  searchSource: "platform",
};

export const mockModelOptions: ModelOption[] = [
  {
    value: "mock-claude-sonnet:mock-provider",
    modelId: "mock-claude-sonnet",
    providerId: "mock-provider",
    label: "Claude Sonnet 演示",
    description: "演示模型 · 支持工具调用、视觉输入和思考过程",
    billingRatio: 10,
    supportTools: true,
    supportVision: true,
    supportThinking: true,
  },
  {
    value: "mock-gpt-4o:mock-provider",
    modelId: "mock-gpt-4o",
    providerId: "mock-provider",
    label: "GPT-4o 演示",
    description: "演示模型 · 支持多模态和工具调试",
    billingRatio: 3,
    supportTools: true,
    supportVision: true,
    supportThinking: false,
  },
];

export const mockSearchCredentials: WebSearchCredential[] = [
  {
    user_id: "10001",
    provider: "4get_ddg",
    source: "platform",
    is_member: false,
    api_key_masked: "",
    api_key_fingerprint: "",
    is_active: true,
    created_at: "2026-06-18T08:00:00.000Z",
    updated_at: "2026-06-18T08:00:00.000Z",
  },
  {
    user_id: "10001",
    provider: "exa",
    source: "custom",
    is_member: false,
    api_key_masked: "exa_***demo",
    api_key_fingerprint: "mock-exa-demo",
    is_active: false,
    created_at: "2026-06-18T08:05:00.000Z",
    updated_at: "2026-06-18T08:05:00.000Z",
  },
];

export const mockMessages: ChatAppMessage[] = [
  {
    id: "msg-user-1",
    role: "user",
    metadata: undefined,
    createdAt: "2026-06-18T08:40:24.000Z",
    parts: [
      {
        type: "text",
        text: "帮我查一下最近适合做 AI 搜索结果补全的两个方向，并把 GitHub 仓库和论文候选分开。",
      },
    ],
  },
  {
    id: "msg-assistant-1",
    role: "assistant",
    metadata: undefined,
    createdAt: "2026-06-18T08:40:31.000Z",
    parts: [
      {
        type: "step-start",
      },
      {
        type: "reasoning",
        text:
          "我会先把候选拆成论文和 GitHub 仓库两类，只对有明确 DOI、OpenAlex ID、标题或 owner/repo 的结果做补全。评分上更看重可验证元数据，示例：$$score = 0.7 \\times relevance + 0.3 \\times confidence$$。",
      },
      {
        type: "tool-web_search",
        toolCallId: "toolcall_search_1",
        state: "output-available",
        input: {
          query: "AI search result enrichment github repository papers",
          max_results: 10,
        },
        output: {
          output:
            "<web_search_result>\n" +
            "  <status>ok</status>\n" +
            "  <query>AI search result enrichment github repository papers</query>\n" +
            "  <candidates>\n" +
            "    <item>\n" +
            "      <rank>1</rank>\n" +
            "      <title>OpenAlex documentation</title>\n" +
            "      <url>https://docs.openalex.org/</url>\n" +
            "      <candidate_type>paper_metadata_source</candidate_type>\n" +
            "    </item>\n" +
            "    <item>\n" +
            "      <rank>2</rank>\n" +
            "      <title>PyGithub</title>\n" +
            "      <url>https://github.com/PyGithub/PyGithub</url>\n" +
            "      <candidate_type>github_repository</candidate_type>\n" +
            "    </item>\n" +
            "  </candidates>\n" +
            "  <suggested_actions>\n" +
            "    <item>\n" +
            "      <tool_name>paper_hydrate</tool_name>\n" +
            "      <priority>LOW</priority>\n" +
            "    </item>\n" +
            "    <item>\n" +
            "      <tool_name>github_hydrate</tool_name>\n" +
            "      <priority>LOW</priority>\n" +
            "    </item>\n" +
            "  </suggested_actions>\n" +
            "</web_search_result>",
          debug_output: {
            candidates: [
              {
                title: "OpenAlex documentation",
                url: "https://docs.openalex.org/",
              },
              {
                title: "PyGithub",
                url: "https://github.com/PyGithub/PyGithub",
              },
            ],
            suggested_actions: [
              { tool_name: "paper_hydrate", priority: "LOW" },
              { tool_name: "github_hydrate", priority: "LOW" },
            ],
          },
          model_consumed_xml:
            "<web_search_result>\n" +
            "  <status>ok</status>\n" +
            "  <query>AI search result enrichment github repository papers</query>\n" +
            "  <candidates>\n" +
            "    <item>\n" +
            "      <rank>1</rank>\n" +
            "      <title>OpenAlex documentation</title>\n" +
            "      <url>https://docs.openalex.org/</url>\n" +
            "      <candidate_type>paper_metadata_source</candidate_type>\n" +
            "    </item>\n" +
            "    <item>\n" +
            "      <rank>2</rank>\n" +
            "      <title>PyGithub</title>\n" +
            "      <url>https://github.com/PyGithub/PyGithub</url>\n" +
            "      <candidate_type>github_repository</candidate_type>\n" +
            "    </item>\n" +
            "  </candidates>\n" +
            "  <suggested_actions>\n" +
            "    <item>\n" +
            "      <tool_name>paper_hydrate</tool_name>\n" +
            "      <priority>LOW</priority>\n" +
            "    </item>\n" +
            "    <item>\n" +
            "      <tool_name>github_hydrate</tool_name>\n" +
            "      <priority>LOW</priority>\n" +
            "    </item>\n" +
            "  </suggested_actions>\n" +
            "</web_search_result>",
        },
      },
      {
        type: "tool-paper_hydrate",
        toolCallId: "toolcall_paper_1",
        state: "output-available",
        input: {
          title: "Tool-Augmented Language Models for Search",
        },
        output: {
          output:
            "<paper_hydrate_result>\n" +
            "  <status>hydrated</status>\n" +
            "  <paper>\n" +
            "    <title>Tool-Augmented Language Models for Search and Retrieval</title>\n" +
            "    <authors>\n" +
            "      <item>A. Chen</item>\n" +
            "      <item>B. Morris</item>\n" +
            "      <item>L. Patel</item>\n" +
            "    </authors>\n" +
            "    <year>2026</year>\n" +
            "    <venue>ACL Findings</venue>\n" +
            "    <doi>10.9999/fake.2026.123</doi>\n" +
            "    <openalex_id>https://openalex.org/W1234567890</openalex_id>\n" +
            "    <open_access>true</open_access>\n" +
            "    <cited_by_count>14</cited_by_count>\n" +
            "    <concepts_or_topics>\n" +
            "      <item>retrieval augmentation</item>\n" +
            "      <item>tool use</item>\n" +
            "      <item>ranking</item>\n" +
            "    </concepts_or_topics>\n" +
            "    <source_updated_at>2026-06-16T11:14:12Z</source_updated_at>\n" +
            "  </paper>\n" +
            "</paper_hydrate_result>",
          debug_output: {
            status: "hydrated",
            paper: {
              title: "Tool-Augmented Language Models for Search and Retrieval",
              authors: ["A. Chen", "B. Morris", "L. Patel"],
              year: 2026,
              venue: "ACL Findings",
              doi: "10.9999/fake.2026.123",
              openalex_id: "https://openalex.org/W1234567890",
              open_access: true,
              cited_by_count: 14,
              concepts_or_topics: ["retrieval augmentation", "tool use", "ranking"],
              source_updated_at: "2026-06-16T11:14:12Z",
            },
          },
          model_consumed_xml:
            "<paper_hydrate_result>\n" +
            "  <status>hydrated</status>\n" +
            "  <paper>\n" +
            "    <title>Tool-Augmented Language Models for Search and Retrieval</title>\n" +
            "    <authors>\n" +
            "      <item>A. Chen</item>\n" +
            "      <item>B. Morris</item>\n" +
            "      <item>L. Patel</item>\n" +
            "    </authors>\n" +
            "    <year>2026</year>\n" +
            "    <venue>ACL Findings</venue>\n" +
            "    <doi>10.9999/fake.2026.123</doi>\n" +
            "    <openalex_id>https://openalex.org/W1234567890</openalex_id>\n" +
            "    <open_access>true</open_access>\n" +
            "    <cited_by_count>14</cited_by_count>\n" +
            "    <concepts_or_topics>\n" +
            "      <item>retrieval augmentation</item>\n" +
            "      <item>tool use</item>\n" +
            "      <item>ranking</item>\n" +
            "    </concepts_or_topics>\n" +
            "    <source_updated_at>2026-06-16T11:14:12Z</source_updated_at>\n" +
            "  </paper>\n" +
            "</paper_hydrate_result>",
        },
      },
      {
        type: "tool-github_hydrate",
        toolCallId: "toolcall_github_1",
        state: "output-error",
        input: {
          repository: "anthropics/skills",
          url: "https://github.com/anthropics/skills/tree/main/skills/frontend-design",
        },
        errorText:
          "GithubException 403: API rate limit exceeded for 203.0.113.12. Raw payload: {\"message\":\"API rate limit exceeded\",\"documentation_url\":\"https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting\"}",
      },
      {
        type: "text",
        text:
          "我先给你两类候选：\n\n1. **论文方向**\n- 更适合做结构化补全的是 OpenAlex 这类稳定元数据源，重点拿 title、authors、venue、abstract、topics 和 OA 信息。\n- 如果 title search 命中不够干净，应该把结果标成 `partial`，而不是在工具层硬猜。\n\n2. **GitHub 仓库方向**\n- 对 repo 卡片最有价值的是 topics、license、stars、forks、default branch 和最近 push 时间。\n- 这类信息天然适合单独的 hydration tool，因为它和正文抓取完全是两条链路。\n\n> 当前 GitHub 调用演示了完整错误视图，方便你对照限流和 token 配置。",
      },
    ],
  },
];
