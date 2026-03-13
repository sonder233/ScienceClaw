import type { FileInfo } from '../api/file';

export type AgentSSEEvent = {
  event: 'tool' | 'step' | 'message' | 'error' | 'done' | 'title' | 'wait' | 'plan' | 'attachments' | 'thinking';
  data: ToolEventData | StepEventData | MessageEventData | ErrorEventData | DoneEventData | TitleEventData | WaitEventData | PlanEventData | ThinkingEventData;
}

export interface BaseEventData {
  event_id: string;
  timestamp: number;
}

/** 工具元数据（图标、分类、描述） */
export interface ToolMetaData {
  icon: string;
  category: string;
  description: string;
  sandbox?: boolean;
}

export interface ToolEventData extends BaseEventData {
  tool_call_id: string;
  name: string;
  status: "calling" | "called";
  function: string;
  args: {[key: string]: any};
  content?: any;
  /** 工具调用耗时（毫秒），仅 status=called 时存在 */
  duration_ms?: number;
  /** 工具元数据（图标、分类、描述） */
  tool_meta?: ToolMetaData;
}

export interface StepEventData extends BaseEventData {
  status: "pending" | "running" | "completed" | "failed"
  id: string
  description: string
  tools?: ToolEventData[]
}

export interface MessageEventData extends BaseEventData {
  content: string;
  role: "user" | "assistant";
  attachments: FileInfo[];
}

export interface ErrorEventData extends BaseEventData {
  error: string;
}

/** 统计信息 */
export interface StatisticsData {
  total_duration_ms?: number;
  tool_call_count?: number;
  input_tokens?: number;
  output_tokens?: number;
  token_count?: number;
}

export interface DoneEventData extends BaseEventData {
  /** 执行统计信息 */
  statistics?: StatisticsData;
}

export interface WaitEventData extends BaseEventData {
}

export interface TitleEventData extends BaseEventData {
  title: string;
}

export interface PlanEventData extends BaseEventData {
  steps: StepEventData[];
}

/** 思考过程事件 */
export interface ThinkingEventData extends BaseEventData {
  content: string;
}