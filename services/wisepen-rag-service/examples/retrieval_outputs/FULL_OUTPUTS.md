# 检索与一跳拓展完整输出

本文件由 retrieval_outputs.py 实际运行生成。应用、快照授权与事实回查使用当前代码；存储、embedding、rerank
使用固定本地替身。分数是演示设定，不代表模型质量。

所有实际序列化字段、默认值、null、metadata、正文、描述和来源 ID 均完整保留。top_k=10 高于案例实际来源数，无任何文本裁剪；完整输入另见
inputs.json。

ChunkHit.metadata 在响应组装时排除 entities、relationships；输入文件保留完整内部 metadata，图检索的顶层
entities/relationships 和 seed_entities 不受影响。

向量 dense/sparse/hybrid 的候选替身刻意返回同一组记录，用于对照输出结构；不能据此推断真实检索模式的召回结果相同。vector_graph_disabled
演示 relations 为 null；其他向量案例启用全图连接查询。

图用例按逻辑 ID 收拢：实体与关系都只返回 id/sources，各来源描述独立保留。内部图运算使用核心模型的结构化端点与谓词，不解析返回
ID。核心 GraphNode.id、GraphEdge.id 是计算属性，仅输入事实的序列化不包含它们；输入文件 computed_entity_ids 供核对。核心
GraphNode.source_id 是来源 Chunk，GraphEdge.source_id 是起点实体，两者含义不同。

## 固定数据

employment-a、employment-b 都记录张三任职于甲公司；supply-c 记录甲公司供应乙公司；private-d 属于其他用户，进入候选和图投影后由真实快照授权过滤。

## graph_local

输入：

```json
{
  "query": "张三在甲公司的任职情况是什么？",
  "mode": "local",
  "top_k": 10
}
```

完整输出：

```json
{
  "entities": [
    {
      "id": "work:Person:张三",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件研发人员"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目负责人"
        }
      ]
    }
  ],
  "relationships": [
    {
      "id": "work:Person:张三::works_at::work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "张三在甲公司任职，负责软件研发。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "张三是甲公司的项目负责人，负责项目交付。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        }
      ]
    }
  ],
  "chunks": [
    {
      "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
      "resource_id": "employment-a",
      "content_revision": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14",
      "section_id": "demo-section-employment-a",
      "section_path": [
        "任职登记"
      ],
      "text": "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
      "rank": 1,
      "score": 0.97,
      "seed_entities": [
        {
          "id": "work:Person:张三",
          "relations": [
            {
              "relation_type": "works_at",
              "direction": "outgoing"
            }
          ]
        },
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "employment-a"
        }
      }
    },
    {
      "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
      "resource_id": "employment-b",
      "content_revision": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd",
      "section_id": "demo-section-employment-b",
      "section_path": [
        "项目负责人"
      ],
      "text": "# 项目负责人\n张三是甲公司的项目负责人，负责项目交付。\n",
      "rank": 2,
      "score": 0.91,
      "seed_entities": [
        {
          "id": "work:Person:张三",
          "relations": [
            {
              "relation_type": "works_at",
              "direction": "outgoing"
            }
          ]
        },
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "employment-b"
        }
      }
    }
  ]
}
```

## graph_global

输入：

```json
{
  "query": "张三在甲公司的任职情况是什么？",
  "mode": "global",
  "top_k": 10
}
```

完整输出：

```json
{
  "entities": [
    {
      "id": "work:Person:张三",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件研发人员"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目负责人"
        }
      ]
    },
    {
      "id": "work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件公司"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目交付单位"
        },
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "软件供应商"
        }
      ]
    }
  ],
  "relationships": [
    {
      "id": "work:Person:张三::works_at::work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "张三在甲公司任职，负责软件研发。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "张三是甲公司的项目负责人，负责项目交付。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        }
      ]
    }
  ],
  "chunks": [
    {
      "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
      "resource_id": "employment-a",
      "content_revision": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14",
      "section_id": "demo-section-employment-a",
      "section_path": [
        "任职登记"
      ],
      "text": "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
      "rank": 1,
      "score": 0.97,
      "seed_entities": [
        {
          "id": "work:Person:张三",
          "relations": [
            {
              "relation_type": "works_at",
              "direction": "outgoing"
            }
          ]
        },
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "employment-a"
        }
      }
    },
    {
      "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
      "resource_id": "employment-b",
      "content_revision": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd",
      "section_id": "demo-section-employment-b",
      "section_path": [
        "项目负责人"
      ],
      "text": "# 项目负责人\n张三是甲公司的项目负责人，负责项目交付。\n",
      "rank": 2,
      "score": 0.91,
      "seed_entities": [
        {
          "id": "work:Person:张三",
          "relations": [
            {
              "relation_type": "works_at",
              "direction": "outgoing"
            }
          ]
        },
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "employment-b"
        }
      }
    },
    {
      "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
      "resource_id": "supply-c",
      "content_revision": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a",
      "section_id": "demo-section-supply-c",
      "section_path": [
        "供应记录"
      ],
      "text": "# 供应记录\n甲公司向乙公司供应软件系统。\n",
      "rank": 3,
      "score": 0.76,
      "seed_entities": [
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        },
        {
          "id": "work:Company:乙公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "supply-c"
        }
      }
    }
  ]
}
```

## graph_hybrid

输入：

```json
{
  "query": "张三在甲公司的任职情况是什么？",
  "mode": "hybrid",
  "top_k": 10
}
```

完整输出：

```json
{
  "entities": [
    {
      "id": "work:Person:张三",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件研发人员"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目负责人"
        }
      ]
    },
    {
      "id": "work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件公司"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目交付单位"
        },
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "软件供应商"
        }
      ]
    }
  ],
  "relationships": [
    {
      "id": "work:Person:张三::works_at::work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "张三在甲公司任职，负责软件研发。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "张三是甲公司的项目负责人，负责项目交付。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        }
      ]
    }
  ],
  "chunks": [
    {
      "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
      "resource_id": "employment-a",
      "content_revision": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14",
      "section_id": "demo-section-employment-a",
      "section_path": [
        "任职登记"
      ],
      "text": "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
      "rank": 1,
      "score": 0.97,
      "seed_entities": [
        {
          "id": "work:Person:张三",
          "relations": [
            {
              "relation_type": "works_at",
              "direction": "outgoing"
            }
          ]
        },
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "employment-a"
        }
      }
    },
    {
      "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
      "resource_id": "employment-b",
      "content_revision": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd",
      "section_id": "demo-section-employment-b",
      "section_path": [
        "项目负责人"
      ],
      "text": "# 项目负责人\n张三是甲公司的项目负责人，负责项目交付。\n",
      "rank": 2,
      "score": 0.91,
      "seed_entities": [
        {
          "id": "work:Person:张三",
          "relations": [
            {
              "relation_type": "works_at",
              "direction": "outgoing"
            }
          ]
        },
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "employment-b"
        }
      }
    },
    {
      "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
      "resource_id": "supply-c",
      "content_revision": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a",
      "section_id": "demo-section-supply-c",
      "section_path": [
        "供应记录"
      ],
      "text": "# 供应记录\n甲公司向乙公司供应软件系统。\n",
      "rank": 3,
      "score": 0.76,
      "seed_entities": [
        {
          "id": "work:Company:甲公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "outgoing"
            },
            {
              "relation_type": "works_at",
              "direction": "incoming"
            }
          ]
        },
        {
          "id": "work:Company:乙公司",
          "relations": [
            {
              "relation_type": "supplies",
              "direction": "incoming"
            }
          ]
        }
      ],
      "metadata": {
        "graph_version": "demo-v1",
        "contextual_context": "固定示例上下文",
        "business": {
          "record": "supply-c"
        }
      }
    }
  ]
}
```

## vector_dense

输入：

```json
{
  "query": "张三在甲公司的任职情况是什么？",
  "mode": "dense",
  "top_k": 10
}
```

完整输出：

```json
[
  {
    "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
    "resource_id": "employment-a",
    "content_revision": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14",
    "section_id": "demo-section-employment-a",
    "section_path": [
      "任职登记"
    ],
    "text": "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
    "rank": 1,
    "score": 0.97,
    "seed_entities": [
      {
        "id": "work:Person:张三",
        "relations": [
          {
            "relation_type": "works_at",
            "direction": "outgoing"
          }
        ]
      },
      {
        "id": "work:Company:甲公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "outgoing"
          },
          {
            "relation_type": "works_at",
            "direction": "incoming"
          }
        ]
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "employment-a"
      }
    }
  },
  {
    "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
    "resource_id": "supply-c",
    "content_revision": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a",
    "section_id": "demo-section-supply-c",
    "section_path": [
      "供应记录"
    ],
    "text": "# 供应记录\n甲公司向乙公司供应软件系统。\n",
    "rank": 2,
    "score": 0.76,
    "seed_entities": [
      {
        "id": "work:Company:甲公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "outgoing"
          },
          {
            "relation_type": "works_at",
            "direction": "incoming"
          }
        ]
      },
      {
        "id": "work:Company:乙公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "incoming"
          }
        ]
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "supply-c"
      }
    }
  }
]
```

## vector_sparse

输入：

```json
{
  "query": "张三在甲公司的任职情况是什么？",
  "mode": "sparse",
  "top_k": 10
}
```

完整输出：

```json
[
  {
    "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
    "resource_id": "employment-a",
    "content_revision": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14",
    "section_id": "demo-section-employment-a",
    "section_path": [
      "任职登记"
    ],
    "text": "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
    "rank": 1,
    "score": 0.97,
    "seed_entities": [
      {
        "id": "work:Person:张三",
        "relations": [
          {
            "relation_type": "works_at",
            "direction": "outgoing"
          }
        ]
      },
      {
        "id": "work:Company:甲公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "outgoing"
          },
          {
            "relation_type": "works_at",
            "direction": "incoming"
          }
        ]
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "employment-a"
      }
    }
  },
  {
    "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
    "resource_id": "supply-c",
    "content_revision": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a",
    "section_id": "demo-section-supply-c",
    "section_path": [
      "供应记录"
    ],
    "text": "# 供应记录\n甲公司向乙公司供应软件系统。\n",
    "rank": 2,
    "score": 0.76,
    "seed_entities": [
      {
        "id": "work:Company:甲公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "outgoing"
          },
          {
            "relation_type": "works_at",
            "direction": "incoming"
          }
        ]
      },
      {
        "id": "work:Company:乙公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "incoming"
          }
        ]
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "supply-c"
      }
    }
  }
]
```

## vector_hybrid

输入：

```json
{
  "query": "张三在甲公司的任职情况是什么？",
  "mode": "hybrid",
  "top_k": 10
}
```

完整输出：

```json
[
  {
    "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
    "resource_id": "employment-a",
    "content_revision": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14",
    "section_id": "demo-section-employment-a",
    "section_path": [
      "任职登记"
    ],
    "text": "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
    "rank": 1,
    "score": 0.97,
    "seed_entities": [
      {
        "id": "work:Person:张三",
        "relations": [
          {
            "relation_type": "works_at",
            "direction": "outgoing"
          }
        ]
      },
      {
        "id": "work:Company:甲公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "outgoing"
          },
          {
            "relation_type": "works_at",
            "direction": "incoming"
          }
        ]
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "employment-a"
      }
    }
  },
  {
    "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
    "resource_id": "supply-c",
    "content_revision": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a",
    "section_id": "demo-section-supply-c",
    "section_path": [
      "供应记录"
    ],
    "text": "# 供应记录\n甲公司向乙公司供应软件系统。\n",
    "rank": 2,
    "score": 0.76,
    "seed_entities": [
      {
        "id": "work:Company:甲公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "outgoing"
          },
          {
            "relation_type": "works_at",
            "direction": "incoming"
          }
        ]
      },
      {
        "id": "work:Company:乙公司",
        "relations": [
          {
            "relation_type": "supplies",
            "direction": "incoming"
          }
        ]
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "supply-c"
      }
    }
  }
]
```

## vector_graph_disabled

输入：

```json
{
  "query": "张三在甲公司的任职情况是什么？",
  "mode": "dense",
  "top_k": 10,
  "graph_enabled": false
}
```

完整输出：

```json
[
  {
    "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
    "resource_id": "employment-a",
    "content_revision": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14",
    "section_id": "demo-section-employment-a",
    "section_path": [
      "任职登记"
    ],
    "text": "# 任职登记\n张三在甲公司任职，负责软件研发。\n",
    "rank": 1,
    "score": 0.97,
    "seed_entities": [
      {
        "id": "work:Person:张三",
        "relations": null
      },
      {
        "id": "work:Company:甲公司",
        "relations": null
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "employment-a"
      }
    }
  },
  {
    "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
    "resource_id": "supply-c",
    "content_revision": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a",
    "section_id": "demo-section-supply-c",
    "section_path": [
      "供应记录"
    ],
    "text": "# 供应记录\n甲公司向乙公司供应软件系统。\n",
    "rank": 2,
    "score": 0.76,
    "seed_entities": [
      {
        "id": "work:Company:甲公司",
        "relations": null
      },
      {
        "id": "work:Company:乙公司",
        "relations": null
      }
    ],
    "metadata": {
      "graph_version": "demo-v1",
      "contextual_context": "固定示例上下文",
      "business": {
        "record": "supply-c"
      }
    }
  }
]
```

## traversal_person

输入：

```json
{
  "node_ids": [
    "work:Person:张三"
  ],
  "direction": "outgoing",
  "relation_limit": 100
}
```

完整输出：

```json
{
  "entities": [
    {
      "id": "work:Person:张三",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件研发人员"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目负责人"
        }
      ]
    },
    {
      "id": "work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件公司"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目交付单位"
        }
      ]
    }
  ],
  "relationships": [
    {
      "id": "work:Person:张三::works_at::work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "张三在甲公司任职，负责软件研发。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "张三是甲公司的项目负责人，负责项目交付。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        }
      ]
    }
  ]
}
```

## traversal_company

输入：

```json
{
  "node_ids": [
    "work:Company:甲公司"
  ],
  "direction": "both",
  "relation_limit": 100
}
```

完整输出：

```json
{
  "entities": [
    {
      "id": "work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件公司"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目交付单位"
        },
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "软件供应商"
        }
      ]
    },
    {
      "id": "work:Company:乙公司",
      "sources": [
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "软件采购方"
        }
      ]
    },
    {
      "id": "work:Person:张三",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件研发人员"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目负责人"
        }
      ]
    }
  ],
  "relationships": [
    {
      "id": "work:Company:甲公司::supplies::work:Company:乙公司",
      "sources": [
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "甲公司向乙公司供应软件系统。",
          "keywords": [
            "供应"
          ],
          "metadata": {}
        }
      ]
    },
    {
      "id": "work:Person:张三::works_at::work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "张三在甲公司任职，负责软件研发。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "张三是甲公司的项目负责人，负责项目交付。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        }
      ]
    }
  ]
}
```

## traversal_shared

输入：

```json
{
  "node_ids": [
    "work:Person:张三",
    "work:Company:甲公司"
  ],
  "direction": "both",
  "relation_limit": 100
}
```

完整输出：

```json
{
  "entities": [
    {
      "id": "work:Person:张三",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件研发人员"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目负责人"
        }
      ]
    },
    {
      "id": "work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "软件公司"
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "项目交付单位"
        },
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "软件供应商"
        }
      ]
    },
    {
      "id": "work:Company:乙公司",
      "sources": [
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "软件采购方"
        }
      ]
    }
  ],
  "relationships": [
    {
      "id": "work:Company:甲公司::supplies::work:Company:乙公司",
      "sources": [
        {
          "chunk_id": "supply-c@1#d71cc9c5cdfea76f673f099e75472b3e7b3d5da57a12fb5abb8f79b7af96926a:chunk:0",
          "description": "甲公司向乙公司供应软件系统。",
          "keywords": [
            "供应"
          ],
          "metadata": {}
        }
      ]
    },
    {
      "id": "work:Person:张三::works_at::work:Company:甲公司",
      "sources": [
        {
          "chunk_id": "employment-a@1#baf82b6a375291f5f8aeadb7a5e2ddfbb6409699408f02406df9790c35b9ca14:chunk:0",
          "description": "张三在甲公司任职，负责软件研发。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        },
        {
          "chunk_id": "employment-b@1#41c6aa8b354baba6646d0394be19591f7398bf24d1d526fdfca28d1112e2f3fd:chunk:0",
          "description": "张三是甲公司的项目负责人，负责项目交付。",
          "keywords": [
            "任职"
          ],
          "metadata": {}
        }
      ]
    }
  ]
}
```
