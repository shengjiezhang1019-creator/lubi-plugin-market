# 可选 Hook 与契约

当前列出 12 个公开 Hook，快照日期 2026-10-23。
清单来自配套宿主的公开 Lifecycle Hook 与模型工具 Hook 目录，不按磁盘上的契约文件数量猜测。
这里不承诺最低宿主版本；具体是否可用，以安装端公开目录及加载校验为准。

收录表单勾选仅供作者说明使用范围，不会自动建立绑定，也不替代源码审核。提交申请时必须至少选择一个实际使用的 Hook，可多选，不提供占位选项。

| Hook | 用途 | 组件类型 |
| --- | --- | --- |
| `AFTER_DANMAKU_REPLY_GENERATION` | 弹幕回复生成后的处理 | command, tool |
| `AFTER_GIFT_SIGNAL_TEXT_GENERATION` | 礼物信号文案生成后的处理 | command, tool |
| `AFTER_SPECULATIVE_REPLY_GENERATION` | 推测回复生成后的处理 | command, tool |
| `BARRAGE_PROCESSING` | 弹幕筛选与处理 | command, tool |
| `BARRAGE_STEP_WEB_SEARCH` | 弹幕思考阶段的联网搜索 | command, tool |
| `BEFORE_DEEP_THINKING` | 深度思考前的规划接入 | command, tool |
| `BEFORE_REPLY_CONTEXT` | 回复前补充参考资料 | command, tool |
| `DOUYIN_LIVE_CONTROL` | 抖音直播连接控制 | command, tool |
| `KNOWLEDGE_QUERY` | 本地知识查询 | command, tool |
| `MEMORY_TERM_LOOKUP` | 记忆术语查询 | command, tool |
| `MESSAGE_INGRESS` | 标准消息接入 | command, tool |
| `PFC_CONTEXT_GATHERING` | 向 PFC 规划模型提供上下文工具 | tool |

## 如何阅读契约

以下 JSON 为宿主 DataSchema 契约描述，字段包含类型、说明、约束以及可选的 required/nullable，不是需要提交到 Issue 的参数。
普通 Lifecycle Hook 通过 LifecycleBindingRequest 绑定组件；模型工具 Hook 由宿主模型按需选择 Tool。

回复资料 Hook 的最小数据示例：

```json
{"text": "播报一下游戏新闻", "stream_id": "当前对话流 ID"}
```

组件成功执行后的结果 data 示例：

```json
{"context": "新闻标题、摘要、日期和原文链接"}
```

该示例对应 BEFORE_REPLY_CONTEXT，其他 Hook 必须使用各自契约，不能套用相同输入。

## AFTER_DANMAKU_REPLY_GENERATION

弹幕被 Planner 选为 reply 后、顺序生成完整候选且发送前的受限文本改写；它失效时沿用原文继续

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "generation_id": {
      "type": "string",
      "description": "本次投机生成的稳定 ID"
    },
    "turn_id": {
      "type": "string",
      "description": "当前 Heart Flow 回合 ID"
    },
    "model_task": {
      "type": "object",
      "description": "最小模型任务快照",
      "constraints": {
        "properties": {
          "task_id": {
            "type": "string",
            "description": "稳定模型任务 ID"
          },
          "model_id": {
            "type": "string",
            "description": "实际模型标识"
          }
        }
      }
    },
    "stream": {
      "type": "object",
      "description": "最小对话流快照",
      "constraints": {
        "properties": {
          "stream_id": {
            "type": "string",
            "description": "稳定对话流 ID"
          },
          "platform": {
            "type": "string",
            "description": "平台标识"
          }
        }
      }
    },
    "target": {
      "type": "object",
      "description": "Core 确认的候选目标",
      "constraints": {
        "properties": {
          "kind": {
            "type": "string",
            "description": "Core 确认的目标种类"
          },
          "target_id": {
            "type": "string",
            "description": "Core 确认的目标 ID"
          }
        }
      }
    },
    "candidate": {
      "type": "object",
      "description": "完整、不可见的投机回复候选",
      "constraints": {
        "properties": {
          "text": {
            "type": "string",
            "description": "完整、尚未可见的候选正文"
          },
          "segments": {
            "type": "list",
            "description": "发送顺序冻结的候选文本段投影",
            "constraints": {
              "items": {
                "type": "object",
                "constraints": {
                  "properties": {
                    "type": {
                      "type": "string",
                      "description": "段类型"
                    },
                    "text": {
                      "type": "string",
                      "description": "仅开放给审核的文本投影"
                    }
                  }
                }
              }
            }
          },
          "model_id": {
            "type": "string",
            "description": "实际生成模型 ID"
          },
          "input_tokens": {
            "type": "integer",
            "description": "输入 token 数；未知时为 0"
          },
          "output_tokens": {
            "type": "integer",
            "description": "输出 token 数；未知时为 0"
          },
          "elapsed_ms": {
            "type": "integer",
            "description": "完整候选生成耗时"
          }
        }
      }
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "description": "保留原候选或用受限文本替换它",
      "constraints": {
        "enum": [
          "keep",
          "replace"
        ]
      }
    },
    "replacement_text": {
      "type": "string",
      "description": "replace 时的非空完整替代正文，最多 256 字符；keep 时必须为 null",
      "nullable": true
    },
    "annotations": {
      "type": "list",
      "description": "只读审计标注",
      "constraints": {
        "items": {
          "type": "object",
          "constraints": {
            "properties": {
              "code": {
                "type": "string",
                "description": "稳定审核标注代码"
              },
              "message": {
                "type": "string",
                "description": "面向审计的简短说明"
              }
            }
          }
        }
      }
    }
  }
}
```

## AFTER_GIFT_SIGNAL_TEXT_GENERATION

大额礼物代码直调的情绪文案生成后、写入情绪状态前的受限文本改写；它失效时沿用模板原文继续

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "signal_id": {
      "type": "string",
      "description": "本次直调的稳定审计 ID"
    },
    "room_stream_id": {
      "type": "string",
      "description": "所属直播间的稳定流 ID"
    },
    "gift": {
      "type": "object",
      "description": "本次触发直调的礼物快照",
      "constraints": {
        "properties": {
          "gift_name": {
            "type": "string",
            "description": "礼物名"
          },
          "combo_count": {
            "type": "integer",
            "description": "连击数，至少为 1"
          },
          "diamond_value": {
            "type": "number",
            "description": "本次礼物的钻石总价值"
          }
        }
      }
    },
    "actor": {
      "type": "object",
      "description": "裁剪后的最小送礼人快照",
      "constraints": {
        "properties": {
          "nickname": {
            "type": "string",
            "description": "展示昵称；缺失时为「观众」"
          },
          "badge_label": {
            "type": "string",
            "description": "灯牌标签；无灯牌时为空串"
          }
        }
      }
    },
    "candidate_text": {
      "type": "string",
      "description": "Core 模板生成的待改写文案正文"
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "description": "保留模板原文或用受限文本替换它",
      "constraints": {
        "enum": [
          "keep",
          "replace"
        ]
      }
    },
    "replacement_text": {
      "type": "string",
      "description": "replace 时的完整替代正文，不超过 80 字；keep 时必须为 null",
      "nullable": true
    }
  }
}
```

## AFTER_SPECULATIVE_REPLY_GENERATION

完整投机回复候选形成后、向任何可见通道放行前的受限文本改写；它失效时沿用原文继续

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "generation_id": {
      "type": "string",
      "description": "本次投机生成的稳定 ID"
    },
    "turn_id": {
      "type": "string",
      "description": "当前 Heart Flow 回合 ID"
    },
    "model_task": {
      "type": "object",
      "description": "最小模型任务快照",
      "constraints": {
        "properties": {
          "task_id": {
            "type": "string",
            "description": "稳定模型任务 ID"
          },
          "model_id": {
            "type": "string",
            "description": "实际模型标识"
          }
        }
      }
    },
    "stream": {
      "type": "object",
      "description": "最小对话流快照",
      "constraints": {
        "properties": {
          "stream_id": {
            "type": "string",
            "description": "稳定对话流 ID"
          },
          "platform": {
            "type": "string",
            "description": "平台标识"
          }
        }
      }
    },
    "target": {
      "type": "object",
      "description": "Core 确认的候选目标",
      "constraints": {
        "properties": {
          "kind": {
            "type": "string",
            "description": "Core 确认的目标种类"
          },
          "target_id": {
            "type": "string",
            "description": "Core 确认的目标 ID"
          }
        }
      }
    },
    "candidate": {
      "type": "object",
      "description": "完整、不可见的投机回复候选",
      "constraints": {
        "properties": {
          "text": {
            "type": "string",
            "description": "完整、尚未可见的候选正文"
          },
          "segments": {
            "type": "list",
            "description": "发送顺序冻结的候选文本段投影",
            "constraints": {
              "items": {
                "type": "object",
                "constraints": {
                  "properties": {
                    "type": {
                      "type": "string",
                      "description": "段类型"
                    },
                    "text": {
                      "type": "string",
                      "description": "仅开放给审核的文本投影"
                    }
                  }
                }
              }
            }
          },
          "model_id": {
            "type": "string",
            "description": "实际生成模型 ID"
          },
          "input_tokens": {
            "type": "integer",
            "description": "输入 token 数；未知时为 0"
          },
          "output_tokens": {
            "type": "integer",
            "description": "输出 token 数；未知时为 0"
          },
          "elapsed_ms": {
            "type": "integer",
            "description": "完整候选生成耗时"
          }
        }
      }
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "description": "保留原候选或用受限文本替换它",
      "constraints": {
        "enum": [
          "keep",
          "replace"
        ]
      }
    },
    "replacement_text": {
      "type": "string",
      "description": "replace 时的非空完整替代正文，最多 256 字符；keep 时必须为 null",
      "nullable": true
    },
    "annotations": {
      "type": "list",
      "description": "只读审计标注",
      "constraints": {
        "items": {
          "type": "object",
          "constraints": {
            "properties": {
              "code": {
                "type": "string",
                "description": "稳定审核标注代码"
              },
              "message": {
                "type": "string",
                "description": "面向审计的简短说明"
              }
            }
          }
        }
      }
    }
  }
}
```

## BARRAGE_PROCESSING

组织完整弹幕处理，返回候选或等待结果；缺少健康绑定时关闭弹幕自动回复闸门

允许的组件类型：command, tool。

绑定数量：最少 1，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "stream_id": {
      "type": "string",
      "description": "当前聊天流 ID"
    },
    "session_id": {
      "type": "string",
      "description": "当前直播场次 ID；空值表示没有可处理场次"
    },
    "last_processed_time": {
      "type": "number",
      "description": "主流程已处理时间，作为插件游标下限"
    },
    "recently_replied_topics": {
      "type": "list",
      "constraints": {
        "items": {
          "type": "string"
        }
      },
      "description": "近期已经回复的话题，用于内部筛选去重"
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "object",
      "nullable": true,
      "constraints": {
        "properties": {
          "batch": {
            "constraints": {
              "items": {
                "constraints": {
                  "allow_extra": true,
                  "properties": {}
                },
                "type": "object"
              }
            },
            "type": "list",
            "description": "本轮原始数据库消息的纯 JSON 快照，供主流程还原回复上下文"
          },
          "candidates_text_preview": {
            "type": "string",
            "description": "筛选候选的文本预览"
          },
          "llm_model": {
            "type": "string",
            "description": "筛选模型名称"
          },
          "llm_raw_output": {
            "type": "string",
            "description": "内部筛选输出的展示记录"
          },
          "min_for_llm": {
            "type": "integer",
            "description": "本轮触发模型筛选的目标条数"
          },
          "rate_per_min": {
            "type": "number",
            "description": "弹幕速率，单位条每分钟"
          },
          "rate_sample_minutes": {
            "type": "number",
            "description": "速率采样时长，单位分钟"
          },
          "reasoning": {
            "type": "string",
            "description": "处理决定的展示说明"
          },
          "selected_message_ids": {
            "constraints": {
              "items": {
                "type": "string"
              }
            },
            "type": "list",
            "description": "选中的稳定消息 ID"
          },
          "selected_topic": {
            "nullable": true,
            "type": "string",
            "description": "选中消息对应的话题"
          },
          "selection_committed_at": {
            "nullable": true,
            "type": "number",
            "description": "筛选决定完成时间"
          },
          "should_reply": {
            "type": "boolean",
            "description": "是否建议进入后续回复规划"
          },
          "similarity_threshold_used": {
            "type": "number",
            "description": "本轮使用的话题相似度阈值"
          },
          "skip_reason": {
            "type": "string",
            "description": "未进入回复的稳定原因代码"
          },
          "time_window_end": {
            "type": "number",
            "description": "本轮处理窗口终点"
          },
          "time_window_start": {
            "type": "number",
            "description": "本轮处理窗口起点"
          },
          "topic_center": {
            "nullable": true,
            "type": "string",
            "description": "本批弹幕的中心话题"
          }
        }
      },
      "description": "完整处理结果；探测、等待或失效时为 null"
    }
  }
}
```

## BARRAGE_STEP_WEB_SEARCH

弹幕脑执行 web_search 子步骤时的联网检索；不决定要不要搜、搜几次，也不生成回复。失效或结果非法时这一步记为无联网结果，管线照常继续

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "step_id": {
      "type": "string",
      "description": "本次子步骤的稳定 ID，同时作为审计 ID"
    },
    "stream_id": {
      "type": "string",
      "description": "所属聊天流的稳定 ID"
    },
    "path": {
      "type": "string",
      "description": "子步骤来自哪条弹幕脑管线",
      "constraints": {
        "enum": [
          "filter",
          "topic"
        ]
      }
    },
    "query": {
      "type": "string",
      "description": "规划器给出的检索短句；Core 保证非空"
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "content": {
      "type": "string",
      "description": "供回复生成参考的检索摘要，不超过 2000 字；没有可用结果时为 null",
      "nullable": true
    }
  }
}
```

## BEFORE_DEEP_THINKING

深度思考执行前为已冻结的思考输入补充结构化事实与规划约束；不选择回合路径、不生成计划或回复、不修改预算。失效或结果非法时沿用原始思考输入

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "thinking_id": {
      "type": "string",
      "description": "本次深度思考的稳定 ID"
    },
    "reasoning_mode": {
      "type": "string",
      "description": "本轮走哪条深度路径",
      "constraints": {
        "enum": [
          "hfc",
          "pfc"
        ]
      }
    },
    "stream": {
      "type": "object",
      "description": "最小 stream 快照",
      "constraints": {
        "properties": {
          "stream_id": {
            "type": "string",
            "description": "当前 stream 稳定 ID"
          },
          "platform": {
            "type": "string",
            "description": "已注册平台"
          }
        }
      }
    },
    "user": {
      "type": "object",
      "description": "Core 裁剪的最小用户快照",
      "constraints": {
        "properties": {
          "user_id": {
            "type": "string",
            "description": "获准公开的用户 ID；缺失时为 null",
            "nullable": true
          }
        }
      }
    },
    "anchor_message": {
      "type": "object",
      "description": "触发本轮深度思考的锚定输入",
      "constraints": {
        "properties": {
          "message_id": {
            "type": "string",
            "description": "稳定消息 ID"
          },
          "normalized_text": {
            "type": "string",
            "description": "受限长度的规范化文本"
          }
        }
      }
    },
    "thinking_input": {
      "type": "object",
      "description": "Core 构造的结构化思考输入",
      "constraints": {
        "properties": {
          "trigger_reason": {
            "type": "string",
            "description": "稳定触发原因码",
            "constraints": {
              "enum": [
                "planner_should_think"
              ]
            }
          },
          "topic_center": {
            "type": "string",
            "description": "当前主题中心；Core 未识别时为 null",
            "nullable": true
          },
          "context_messages": {
            "type": "list",
            "description": "按时间升序的受限只读上下文",
            "constraints": {
              "items": {
                "type": "object",
                "constraints": {
                  "properties": {
                    "message_id": {
                      "type": "string",
                      "description": "稳定消息 ID"
                    },
                    "role": {
                      "type": "string",
                      "description": "该条上下文的发言角色",
                      "constraints": {
                        "enum": [
                          "user",
                          "bot"
                        ]
                      }
                    },
                    "normalized_text": {
                      "type": "string",
                      "description": "受限长度的规范化文本"
                    }
                  }
                }
              }
            }
          }
        }
      }
    },
    "budget": {
      "type": "object",
      "description": "Core 计算的本轮硬预算；Tool 不得请求修改",
      "constraints": {
        "properties": {
          "max_steps": {
            "type": "integer",
            "description": "本轮允许的最大子步骤数"
          }
        }
      }
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "facts": {
      "type": "list",
      "description": "结构化事实，最多 8 条；超出或重复 key 会被框架判为非法结果并整份丢弃",
      "constraints": {
        "items": {
          "type": "object",
          "constraints": {
            "properties": {
              "key": {
                "type": "string",
                "description": "稳定事实键，最长 64 字符"
              },
              "value": {
                "type": "string",
                "description": "事实值的文本表示"
              },
              "source": {
                "type": "string",
                "description": "事实来源标识"
              }
            }
          }
        }
      }
    },
    "constraints": {
      "type": "list",
      "description": "规划约束，最多 4 条；超出或重复 code 会被框架判为非法结果并整份丢弃",
      "constraints": {
        "items": {
          "type": "object",
          "constraints": {
            "properties": {
              "code": {
                "type": "string",
                "description": "稳定约束码，最长 64 字符"
              },
              "instruction": {
                "type": "string",
                "description": "面向规划的一句话约束，最长 120 字符"
              }
            }
          }
        }
      }
    },
    "source_id": {
      "type": "string",
      "description": "贡献来源标识，供审计归因"
    },
    "reason_code": {
      "type": "string",
      "description": "稳定原因码；无贡献时也必须给出"
    }
  }
}
```

## BEFORE_REPLY_CONTEXT

回复生成前补充参考资料；不决定回复、不发送消息，失败时继续原回复流程

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "text": {
      "type": "string",
      "description": "当前回复对象的消息正文，不含历史记录"
    },
    "stream_id": {
      "type": "string",
      "description": "宿主确认的当前对话流 ID"
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "context": {
      "type": "string",
      "description": "只读参考资料，最多 6000 字符；不提供资料时为空串"
    }
  }
}
```

## DOUYIN_LIVE_CONTROL

管理抖音监听：start / stop / status；参数按 operation 分别校验

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "operation": {
      "type": "string",
      "description": "管理操作",
      "constraints": {
        "enum": [
          "start",
          "stop",
          "status"
        ]
      }
    },
    "params": {
      "type": "object",
      "description": "按操作分别校验的参数",
      "constraints": {
        "properties": {},
        "allow_extra": true
      }
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "object",
      "description": "纯 JSON 管理结果",
      "constraints": {
        "properties": {},
        "allow_extra": true
      }
    }
  }
}
```

## KNOWLEDGE_QUERY

查询本地知识；可选单绑定，无默认目标，缺绑定或失败时不补充知识，主流程继续

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "调用方已经确定的非空查询词"
    },
    "top_k": {
      "type": "integer",
      "description": "最多返回的候选数，由宿主归一化到 1 至 20"
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "source": {
      "type": "string",
      "description": "知识提供方的展示标识"
    },
    "query": {
      "type": "string",
      "description": "回显本次查询词，宿主检查与输入一致"
    },
    "content": {
      "type": "string",
      "description": "供后续回复参考的知识正文；未命中为空字符串"
    }
  }
}
```

## MEMORY_TERM_LOOKUP

记忆总结语义增强时查询模型看不懂的单个术语；不决定要不要增强、查哪些词，也不改写记忆。失效或结果非法时该术语不补充解释，总结照常继续

允许的组件类型：command, tool。

绑定数量：最少 0，最多 1。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "lookup_id": {
      "type": "string",
      "description": "本次查询的稳定审计 ID"
    },
    "stream_id": {
      "type": "string",
      "description": "被总结的聊天流稳定 ID"
    },
    "term": {
      "type": "string",
      "description": "模型表示看不懂的术语、梗或缩写；Core 保证非空"
    },
    "needed_context": {
      "type": "string",
      "description": "模型自述需要的背景知识；没有时为空串"
    },
    "context": {
      "type": "string",
      "description": "术语出现的原文片段，最多 1000 字"
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "explanation": {
      "type": "string",
      "description": "术语解释，不超过 1000 字；查不到时为 null",
      "nullable": true
    }
  }
}
```

## MESSAGE_INGRESS

把已认证平台原始消息转换成 MaiBot 标准消息

允许的组件类型：command, tool。

绑定数量：最少 0，最多 不限。

输入契约：

```json
{
  "type": "object",
  "properties": {
    "source_id": {
      "type": "string",
      "description": "Core 已认证的消息来源 ID"
    },
    "platform": {
      "type": "string",
      "description": "Core 已认证的平台 ID"
    },
    "external_message_id": {
      "type": "string",
      "description": "平台侧稳定消息 ID"
    },
    "payload": {
      "type": "object",
      "description": "来源平台原始 JSON 快照",
      "constraints": {
        "properties": {},
        "allow_extra": true
      }
    }
  }
}
```

返回 data 契约：

```json
{
  "type": "object",
  "properties": {
    "decision": {
      "type": "string",
      "description": "是否接受来源消息",
      "constraints": {
        "enum": [
          "accepted",
          "rejected"
        ]
      }
    },
    "message": {
      "type": "object",
      "description": "转换后的 MessageRecv 兼容消息；直播来源携带 live_event.py 定义的 live_event 封装",
      "nullable": true,
      "constraints": {
        "properties": {},
        "allow_extra": true
      }
    }
  }
}
```

## PFC_CONTEXT_GATHERING

向 PFC 当前规划轮次提供记忆、本地知识库和联网搜索 Tool；PFC 自己的模型按需选择 Tool，并继续自己的模型循环

允许的组件类型：tool。

模型工具接入点不定义统一的节点输入和回流 data 契约；每个 Tool 声明自己的模型参数，按需由模型调用。

## 清单维护

宿主新增或修改公开 Hook 后，同步更新本目录的 hook-contracts.json、本文及 Issue 表单选项。不能只新增下拉选项而漏掉契约。
