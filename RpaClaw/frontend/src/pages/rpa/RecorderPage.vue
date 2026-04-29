<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Camera, Terminal, CheckCircle, Radio, Send, Wand2, Bot, Code, X, Globe, FileUp, FileDown, Workflow, CloudUpload, ArrowRight, Loader2, FileText, AlertCircle, ChevronDown, ChevronUp, ClipboardCheck, Paperclip } from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import RpaFlowGuide from '@/components/rpa/RpaFlowGuide.vue';
import RpaStepTimeline from '@/components/rpa/RpaStepTimeline.vue';
import { getBackendWsUrl } from '@/utils/sandbox';
import {
  getFrameSizeFromMetadata,
  getInputSizeFromMetadata,
  mapClientPointToViewportPoint,
  type ScreencastFrameMetadata,
  type ScreencastSize,
} from '@/utils/screencastGeometry';
import {
  buildScreencastReconnectMessage,
  getScreencastReconnectDelayMs,
  getScreencastReconnectNoticeDelayMs,
  isTerminalScreencastClose,
  shouldShowScreencastReconnectNotice,
} from '@/utils/screencastReconnect';
import { buildRpaToolEditorLocation } from '@/utils/rpaMcpConvert';
import {
  getInitialRpaAgentProgress,
  getRpaAgentProgressForEvent,
  type RpaAgentMessageStatus,
} from '@/utils/rpaAgentProgress';
import {
  applyRpaAssistantRunEvent,
  createRpaAssistantRun,
  type RpaAssistantRun,
  type RpaAssistantRunItem,
  type RpaAssistantRound,
} from '@/utils/rpaAssistantRun';
import {
  getManualRecordingDiagnostics,
  isRpaTimelineStepDeletable,
  mapRpaConfigureDisplaySteps,
} from '@/utils/rpaConfigureTimeline';

const router = useRouter();
const route = useRoute();
const launchSource = ref(typeof route.query.source === 'string' ? route.query.source : '');

const sessionId = ref<string | null>(null);
const sandboxSessionId = ref<string>('');
const isRecording = ref(true);
const recordingTime = ref('00:00');
const timerInterval = ref<any>(null);
const loading = ref(true);
const error = ref<string | null>(null);

const canvasRef = ref<HTMLCanvasElement | null>(null);
const fileBridgeInputRef = ref<HTMLInputElement | null>(null);
const screencastFrameSize = ref<ScreencastSize>({ width: 1280, height: 720 });
const screencastInputSize = ref<ScreencastSize>({ width: 1280, height: 720 });
let screencastWs: WebSocket | null = null;
let screencastReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectNoticeTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectAttempts = 0;
let screencastReconnectStartedAt = 0;
let shouldReconnectScreencast = true;
let currentScreencastSessionId: string | null = null;
let lastMoveTime = 0;
interface PendingFileInput {
  token: string;
  accept: string;
  multiple: boolean;
  name?: string;
  id?: string;
}
interface DownloadableFile {
  step_id: string;
  filename: string;
  path: string;
  size?: number;
  result_key: string;
  kind?: string;
}
const pendingFileInput = ref<PendingFileInput | null>(null);
const fileBridgeStatus = ref('');
const fileBridgeMode = ref<'fixed' | 'path' | 'download'>('fixed');
const fileBridgePath = ref('');
const fileBridgeFiles = ref<File[]>([]);
const downloadableFiles = ref<DownloadableFile[]>([]);
const fileBridgeDragging = ref(false);
const fileBridgeBusy = ref(false);
interface BrowserTab {
  tab_id: string;
  title: string;
  url: string;
  opener_tab_id?: string | null;
  status: string;
  active: boolean;
}

const tabs = ref<BrowserTab[]>([]);
const activeTabId = ref<string | null>(null);
const addressInput = ref('about:blank');
const isAddressEditing = ref(false);
const isNavigating = ref(false);
const MOVE_THROTTLE = 50; // 50ms 节流

const steps = ref<any[]>([
  { id: '0', title: '初始化环境', description: '正在配置沙箱录制环境...', status: 'active' }
]);
const acceptedTraces = ref<any[]>([]);
const recordingDiagnostics = ref<any[]>([]);

const parseLocator = (raw: unknown) => {
  if (!raw) return null;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return { method: 'css', value: raw };
    }
  }
  return raw as any;
};

const formatLocator = (raw: unknown): string => {
  const locator = parseLocator(raw);
  if (!locator) return 'No locator';
  if (locator.method === 'role') {
    return locator.name ? `role=${locator.role}[name="${locator.name}"]` : `role=${locator.role}`;
  }
  if (locator.method === 'nested') {
    return `${formatLocator(locator.parent)} >> ${formatLocator(locator.child)}`;
  }
  if (locator.method === 'nth') {
    const baseLocator = locator.locator || locator.base;
    const prefix = baseLocator ? `${formatLocator(baseLocator)} >> ` : '';
    return `${prefix}nth=${locator.index}`;
  }
  if (locator.method === 'css') return locator.value || 'css';
  return `${locator.method || 'locator'}:${locator.value || locator.name || ''}`;
};

const formatFramePath = (framePath?: string[]) => {
  if (!framePath?.length) return 'Main frame';
  return framePath.join(' -> ');
};

const mapServerSteps = (serverSteps: any[]) => ([
  { id: '0', title: '环境就绪', description: '已成功启动 Playwright 浏览器', status: 'completed' },
  ...serverSteps.map((s: any, i: number) => ({
    id: String(i + 1),
    title: s.description || s.action,
    description: s.source === 'ai' ? (s.prompt || s.description || 'AI 操作') : `${s.action} -> ${formatLocator(s.target || s.label || '')}`,
    status: 'completed',
    action: s.action,
    value: s.value,
    signals: s.signals || {},
    source: s.source || 'record',
    sensitive: s.sensitive || false,
    locatorSummary: formatLocator(s.target),
    frameSummary: formatFramePath(s.frame_path),
    validationStatus: s.validation?.status || '',
    validationDetails: s.validation?.details || '',
  }))
]);

const formatTraceType = (traceType?: string) => {
  const value = traceType || '';
  if (value === 'ai_operation') return 'AI Trace';
  if (value === 'data_capture') return 'Data Capture';
  if (value === 'dataflow_fill') return 'Dataflow Fill';
  if (value === 'file_transform') return 'File Transform';
  if (value === 'navigation') return 'Navigation';
  if (value === 'manual_action') return 'Manual';
  return value || 'Trace';
};

const mapServerTraces = (serverTraces: any[]) => ([
  { id: '0', title: 'Environment ready', description: 'Playwright browser is ready', status: 'completed', deletable: false },
  ...serverTraces.map((t: any, i: number) => ({
    id: String(i + 1),
    traceId: t.trace_id || '',
    title: t.description || t.user_instruction || formatTraceType(t.trace_type),
    description: t.user_instruction || t.action || formatTraceType(t.trace_type),
    status: 'completed',
    action: t.action,
    signals: t.signals || {},
    source: t.source === 'ai' || t.trace_type === 'ai_operation' || t.trace_type === 'file_transform' ? 'ai' : 'record',
    traceType: t.trace_type,
    sensitive: false,
    deletable: isRpaTimelineStepDeletable({
      source: t.source === 'ai' || t.trace_type === 'ai_operation' ? 'ai' : 'record',
      traceId: t.trace_id || '',
    }),
    locatorSummary: t.locator_candidates?.length ? formatLocator(t.locator_candidates[0]?.locator || t.locator_candidates[0]) : '',
    frameSummary: t.after_page?.url || '',
    validationStatus: t.accepted === false ? 'warning' : 'ok',
    validationDetails: formatTraceType(t.trace_type),
  }))
]);

const mapConfigureTimelineSteps = (session: any) => ([
  { id: '0', title: 'Environment ready', description: 'Playwright browser is ready', status: 'completed', deletable: false },
  ...mapRpaConfigureDisplaySteps(session).map((step: any, index: number) => ({
    id: String(index + 1),
    stepId: step.stepId || '',
    traceId: step.traceId || '',
    title: step.description || step.action,
    description: step.description || step.action,
    status: 'completed',
    source: step.source || 'record',
    sensitive: step.sensitive || false,
    deletable: isRpaTimelineStepDeletable({ source: step.source || 'record', traceId: step.traceId || '' }),
    locatorSummary: formatLocator(step.target),
    frameSummary: formatFramePath(step.frame_path),
    validationStatus: step.validation?.status || '',
    validationDetails: step.validation?.details || '',
  })),
]);

const refreshTimeline = (session: any) => {
  const serverSteps = Array.isArray(session?.steps) ? session.steps : [];
  const serverTraces = Array.isArray(session?.traces) ? session.traces : [];
  acceptedTraces.value = serverTraces;
  recordingDiagnostics.value = getManualRecordingDiagnostics(session);
  if ((Array.isArray(session?.recorded_actions) && session.recorded_actions.length > 0) || serverTraces.length > 0) {
    steps.value = mapConfigureTimelineSteps(session);
    return;
  }
  if (serverSteps.length > 0) {
    steps.value = mapServerSteps(serverSteps);
  }
};

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  time: string;
  script?: string;
  status?: RpaAgentMessageStatus;
  processingLabel?: string;
  error?: string;
  showCode?: boolean;
  actions?: Array<{ description: string; code: string; showCode?: boolean }>;  // Track agent actions
  run?: RpaAssistantRun;
  frameSummary?: string;
  locatorSummary?: string;
  collectionSummary?: string;
  diagnostics?: string[];
}

const chatMessages = ref<ChatMessage[]>([]);
const newMessage = ref('');
const sending = ref(false);
const agentRunning = ref(false);
const chatScrollRef = ref<HTMLElement | null>(null);

interface ChatAttachment {
  staging_id: string;
  filename: string;
  size: number;
}
const chatAttachment = ref<ChatAttachment | null>(null);
const chatAttachmentUploading = ref(false);
const chatAttachmentError = ref('');
const chatAttachmentInputRef = ref<HTMLInputElement | null>(null);
const ALLOWED_ATTACHMENT_EXT = ['.xlsx', '.xlsm', '.xls', '.csv', '.tsv', '.txt', '.docx'];

const triggerChatAttachmentPicker = () => {
  chatAttachmentError.value = '';
  chatAttachmentInputRef.value?.click();
};

const removeChatAttachment = () => {
  chatAttachment.value = null;
  chatAttachmentError.value = '';
  if (chatAttachmentInputRef.value) {
    chatAttachmentInputRef.value.value = '';
  }
};

const handleChatAttachmentChange = async (event: Event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file || !sessionId.value) return;
  const lower = file.name.toLowerCase();
  if (!ALLOWED_ATTACHMENT_EXT.some(ext => lower.endsWith(ext))) {
    chatAttachmentError.value = `不支持的文件类型，仅允许 ${ALLOWED_ATTACHMENT_EXT.join(', ')}`;
    input.value = '';
    return;
  }
  chatAttachmentUploading.value = true;
  chatAttachmentError.value = '';
  try {
    const form = new FormData();
    form.append('file', file);
    const resp = await apiClient.post<{ staging_id: string; filename: string; size: number }>(
      `/rpa/session/${sessionId.value}/chat/attachment`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
    const data = resp.data;
    chatAttachment.value = {
      staging_id: data.staging_id,
      filename: data.filename || file.name,
      size: data.size ?? file.size,
    };
  } catch (err: any) {
    chatAttachmentError.value = err?.response?.data?.detail || err?.message || '上传失败';
  } finally {
    chatAttachmentUploading.value = false;
    input.value = '';
  }
};

const formatAttachmentSize = (bytes: number) => {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

const scrollAssistantToBottom = () => {
  void nextTick(() => {
    const container = chatScrollRef.value;
    if (!container) return;
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  });
};

const runRoundCount = (run?: RpaAssistantRun) => run?.rounds.length || 0;

const getRunTraceCount = (msg: ChatMessage) => (
  msg.run?.traceCount || Math.max(acceptedTraces.value.length, 0)
);

const getRunStatusLabel = (msg: ChatMessage) => {
  if (msg.status === 'error') return '未完成';
  if (msg.status === 'done') return '已完成';
  if (msg.processingLabel) return '处理中';
  return '准备中';
};

const getRunStatusClass = (msg: ChatMessage) => {
  if (msg.status === 'error') return 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300';
  if (msg.status === 'done') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300';
  return 'bg-[#f0dbff] text-[#6900b3] dark:bg-[#831bd7]/20 dark:text-purple-200';
};

const getRoundStatusLabel = (round: RpaAssistantRound) => {
  if (round.status === 'error') return '需要修复';
  if (round.status === 'done') return '已接收';
  return '处理中';
};

const getRoundStatusClass = (round: RpaAssistantRound) => {
  if (round.status === 'error') return 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300';
  if (round.status === 'done') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300';
  return 'bg-[#edeef0] text-gray-700 dark:bg-white/10 dark:text-gray-300';
};

const getRunItemToneClass = (item: RpaAssistantRunItem) => {
  if (item.kind === 'diagnostic') return 'bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-200';
  if (item.kind === 'trace') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200';
  if (item.kind === 'action') return 'bg-[#f6f0ff] text-[#4f00d0] dark:bg-[#831bd7]/15 dark:text-purple-200';
  return 'bg-[#f2f4f6] text-gray-700 dark:bg-white/[0.08] dark:text-gray-300';
};

interface PendingConfirm {
  description: string;
  risk_reason: string;
  code: string;
}
const pendingConfirm = ref<PendingConfirm | null>(null);
let pollInterval: any = null;

const syncAddressBar = (force = false) => {
  if (isAddressEditing.value && !force) return;
  const active = tabs.value.find((tab) => tab.tab_id === activeTabId.value);
  addressInput.value = active?.url || 'about:blank';
};

const syncTabs = (nextTabs: BrowserTab[]) => {
  tabs.value = nextTabs;
  const active = nextTabs.find((tab) => tab.active);
  activeTabId.value = active?.tab_id || null;
  syncAddressBar();
};

const cleanupAssistantText = (text: string, script = '') => {
  let next = text;
  next = next.replace(/^正在分析当前页面\.\.\.\s*/u, '');
  next = next.replace(/```python[\s\S]*?```/gu, '');
  if (script) {
    next = next.replace(script, '');
  }
  return next.trim();
};

const formatAgentDiagnostics = (diagnostics: any[] = []) => diagnostics
  .map((item: any) => {
    const message = String(item?.message || '').trim();
    const rawError = item?.raw?.result?.error ? String(item.raw.result.error).trim() : '';
    if (message && rawError && rawError !== message) return `${message}: ${rawError}`;
    return message || rawError;
  })
  .filter(Boolean);

const initSession = async () => {
  try {
    loading.value = true;
    const sandboxId =
      (route.query.sandboxId as string) ||
      (typeof crypto !== 'undefined' && crypto.randomUUID
        ? `rpa-${crypto.randomUUID()}`
        : `rpa-${Date.now()}`);
    sandboxSessionId.value = sandboxId;

    const resp = await apiClient.post('/rpa/session/start', {
      sandbox_session_id: sandboxId
    });

    if (resp.data.status === 'success') {
      sessionId.value = resp.data.session.id;
      steps.value = [
        { id: '0', title: '环境就绪', description: '已成功启动 Playwright 浏览器，准备录制', status: 'completed' }
      ];
      startTimer();
      startPollingSteps();
      await nextTick();
      connectScreencast(resp.data.session.id);
    }
  } catch (err: any) {
    console.error('Failed to start RPA session:', err);
    error.value = '无法启动录制会话，请检查后端服务。';
  } finally {
    loading.value = false;
  }
};

const startPollingSteps = () => {
  pollInterval = setInterval(async () => {
    if (!sessionId.value) return;
    try {
      const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
      refreshTimeline(resp.data.session || {});
    } catch (err) {
      // Ignore polling errors
    }
  }, 3000);
};

const startTimer = () => {
  let seconds = 0;
  timerInterval.value = setInterval(() => {
    seconds++;
    const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
    const secs = (seconds % 60).toString().padStart(2, '0');
    recordingTime.value = `${mins}:${secs}`;
  }, 1000);
};

onMounted(() => {
  initSession();
});

onBeforeUnmount(() => {
  shouldReconnectScreencast = false;
  if (timerInterval.value) clearInterval(timerInterval.value);
  if (pollInterval) clearInterval(pollInterval);
  disconnectScreencast();
});

const getModifiers = (e: MouseEvent | KeyboardEvent | WheelEvent): number => {
  let mask = 0;
  if (e.altKey) mask |= 1;
  if (e.ctrlKey) mask |= 2;
  if (e.metaKey) mask |= 4;
  if (e.shiftKey) mask |= 8;
  return mask;
};

const drawFrame = (base64Data: string, metadata: ScreencastFrameMetadata) => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const img = new Image();
  img.onload = () => {
    const nextFrameSize = getFrameSizeFromMetadata(metadata, {
      width: img.naturalWidth,
      height: img.naturalHeight,
    });
    const nextInputSize = getInputSizeFromMetadata(metadata, nextFrameSize);
    screencastFrameSize.value = nextFrameSize;
    screencastInputSize.value = nextInputSize;

    if (canvas.width !== nextFrameSize.width) canvas.width = nextFrameSize.width;
    if (canvas.height !== nextFrameSize.height) canvas.height = nextFrameSize.height;
    ctx.drawImage(img, 0, 0);
  };
  img.src = `data:image/jpeg;base64,${base64Data}`;
};

const clearScreencastReconnectTimer = () => {
  if (screencastReconnectTimer !== null) {
    clearTimeout(screencastReconnectTimer);
    screencastReconnectTimer = null;
  }
};

const clearScreencastReconnectNoticeTimer = () => {
  if (screencastReconnectNoticeTimer !== null) {
    clearTimeout(screencastReconnectNoticeTimer);
    screencastReconnectNoticeTimer = null;
  }
};

const hasPendingScreencastReconnect = () => (
  screencastReconnectTimer !== null ||
  (screencastWs !== null && screencastWs.readyState !== WebSocket.OPEN)
);

const disconnectScreencast = () => {
  clearScreencastReconnectTimer();
  clearScreencastReconnectNoticeTimer();
  if (!screencastWs) return;

  const ws = screencastWs;
  screencastWs = null;
  ws.onopen = null;
  ws.onmessage = null;
  ws.onerror = null;
  ws.onclose = null;

  if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
    ws.close();
  }
};

const scheduleScreencastReconnect = () => {
  if (!shouldReconnectScreencast || !currentScreencastSessionId || screencastReconnectTimer !== null) return;

  screencastReconnectAttempts += 1;
  if (screencastReconnectStartedAt <= 0) {
    screencastReconnectStartedAt = Date.now();
  }
  const delay = getScreencastReconnectDelayMs(screencastReconnectAttempts);
  const message = buildScreencastReconnectMessage('录制', delay);
  const noticeDelay = getScreencastReconnectNoticeDelayMs({
    outageStartedAtMs: screencastReconnectStartedAt,
    nowMs: Date.now(),
  });
  clearScreencastReconnectNoticeTimer();
  screencastReconnectNoticeTimer = setTimeout(() => {
    screencastReconnectNoticeTimer = null;
    if (shouldShowScreencastReconnectNotice({
      shouldReconnect: shouldReconnectScreencast,
      hasPendingReconnect: hasPendingScreencastReconnect(),
    })) {
      error.value = message;
    }
  }, noticeDelay);
  screencastReconnectTimer = setTimeout(() => {
    screencastReconnectTimer = null;
    if (!shouldReconnectScreencast || !currentScreencastSessionId) return;
    connectScreencast(currentScreencastSessionId);
  }, delay);
};

const connectScreencast = (sid: string) => {
  currentScreencastSessionId = sid;
  clearScreencastReconnectTimer();
  if (screencastWs) {
    const existing = screencastWs;
    screencastWs = null;
    existing.onopen = null;
    existing.onmessage = null;
    existing.onerror = null;
    existing.onclose = null;
    if (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING) {
      existing.close();
    }
  }
  const wsUrl = getBackendWsUrl(`/rpa/screencast/${sid}`);
  console.log('[RecorderPage] Connecting screencast:', wsUrl);
  const ws = new WebSocket(wsUrl);
  screencastWs = ws;

  ws.onopen = () => {
    if (screencastWs !== ws) return;
    console.log('[RecorderPage] Screencast connected');
    screencastReconnectAttempts = 0;
    screencastReconnectStartedAt = 0;
    clearScreencastReconnectNoticeTimer();
    error.value = null;
  };

  ws.onmessage = async (ev) => {
    if (screencastWs !== ws) return;
    try {
      const msg = JSON.parse(ev.data);
      console.log('[RecorderPage] Screencast message:', msg.type, msg.message || '');
      if (msg.type === 'frame') {
        screencastReconnectStartedAt = 0;
        clearScreencastReconnectNoticeTimer();
        error.value = null;
        drawFrame(msg.data, msg.metadata);
      } else if (msg.type === 'tabs_snapshot') {
        syncTabs(msg.tabs || []);
      } else if (msg.type === 'preview_error') {
        error.value = msg.message || '预览切换失败';
      } else if (msg.type === 'file_input_requested') {
        pendingFileInput.value = {
          token: msg.token,
          accept: msg.accept || '',
          multiple: !!msg.multiple,
          name: msg.name || '',
          id: msg.id || '',
        };
        fileBridgeMode.value = 'fixed';
        fileBridgePath.value = '';
        fileBridgeFiles.value = [];
        fileBridgeBusy.value = false;
        fileBridgeDragging.value = false;
        fileBridgeStatus.value = '';
        try {
          const resp = await apiClient.get(`/rpa/session/${sessionId.value}/downloadable_steps`);
          downloadableFiles.value = resp.data.steps || [];
        } catch {
          downloadableFiles.value = [];
        }
      } else if (msg.type === 'file_input_applied') {
        const names = Array.isArray(msg.filenames) ? msg.filenames.join(', ') : '文件';
        if (msg.source_mode === 'dataflow') fileBridgeStatus.value = `已关联下载文件 ${names}`;
        else fileBridgeStatus.value = msg.source_mode === 'path' ? `已使用路径 ${names}` : `已固定 ${names}`;
        fileBridgeBusy.value = false;
        fileBridgeFiles.value = [];
        pendingFileInput.value = null;
        focusCanvas();
      } else if (msg.type === 'file_input_error') {
        fileBridgeBusy.value = false;
        fileBridgeStatus.value = msg.message || '设置上传文件失败';
      }
    } catch (parseError) {
      console.error('[RecorderPage] Screencast parse error:', parseError);
    }
  };

  ws.onclose = (ev) => {
    if (screencastWs !== ws) return;
    console.warn('[RecorderPage] Screencast closed:', ev.code, ev.reason);
    screencastWs = null;
    if (!shouldReconnectScreencast) return;
    if (isTerminalScreencastClose(ev.code)) {
      shouldReconnectScreencast = false;
      clearScreencastReconnectNoticeTimer();
      error.value = `录制画面流已断开（code=${ev.code}${ev.reason ? `, reason=${ev.reason}` : ''}）`;
      return;
    }
    scheduleScreencastReconnect();
  };

  ws.onerror = (ev) => {
    if (screencastWs !== ws) return;
    console.error('[RecorderPage] Screencast error:', ev);
    if (!shouldReconnectScreencast) {
      error.value = '无法连接录制画面流，请检查后端 screencast WebSocket/代理配置。';
    }
  };
};

const activateTab = async (tabId: string) => {
  if (!sessionId.value || activeTabId.value === tabId) return;
  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/tabs/${tabId}/activate`);
    syncTabs(resp.data.tabs || []);
  } catch (err) {
    console.error('Failed to activate RPA tab:', err);
  }
};

const submitAddressBar = async () => {
  if (!sessionId.value || isNavigating.value) return;
  const rawUrl = addressInput.value.trim();
  if (!rawUrl) {
    syncAddressBar(true);
    return;
  }

  isNavigating.value = true;
  error.value = null;
  try {
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/navigate`, { url: rawUrl });
    syncTabs(resp.data.tabs || []);
    addressInput.value = resp.data.result?.url || addressInput.value;
    focusCanvas();
  } catch (err: any) {
    console.error('Failed to navigate active RPA tab:', err);
    error.value = err.response?.data?.detail || '地址栏导航失败';
  } finally {
    isNavigating.value = false;
    isAddressEditing.value = false;
  }
};

const handleAddressBlur = () => {
  isAddressEditing.value = false;
  syncAddressBar();
};

const isAboutBlankAddress = () => addressInput.value.trim().toLowerCase() === 'about:blank';

const selectAboutBlankAddress = (target: EventTarget | null) => {
  if (!isAboutBlankAddress() || !(target instanceof HTMLInputElement)) return;
  requestAnimationFrame(() => target.select());
};

const handleAddressFocus = (event: FocusEvent) => {
  isAddressEditing.value = true;
  selectAboutBlankAddress(event.target);
};

const handleAddressMouseUp = (event: MouseEvent) => {
  if (!isAboutBlankAddress()) return;
  event.preventDefault();
  selectAboutBlankAddress(event.target);
};

const focusCanvas = () => {
  canvasRef.value?.focus();
};

const readFileAsBase64 = (file: File): Promise<Record<string, any>> => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => {
    const result = String(reader.result || '');
    const comma = result.indexOf(',');
    resolve({
      name: file.name,
      size: file.size,
      mime: file.type,
      last_modified: file.lastModified,
      data_base64: comma >= 0 ? result.slice(comma + 1) : result,
    });
  };
  reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
  reader.readAsDataURL(file);
});

const openFileBridgePicker = () => {
  fileBridgeInputRef.value?.click();
};

const setFileBridgeFiles = (files: File[]) => {
  const request = pendingFileInput.value;
  fileBridgeFiles.value = request?.multiple ? files : files.slice(0, 1);
  fileBridgeStatus.value = '';
};

const handleFileBridgeInputChange = (event: Event) => {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = '';
  if (files.length > 0) setFileBridgeFiles(files);
};

const handleFileBridgeDrop = (event: DragEvent) => {
  fileBridgeDragging.value = false;
  const files = Array.from(event.dataTransfer?.files || []);
  if (files.length > 0) setFileBridgeFiles(files);
};

const cancelFileBridge = () => {
  pendingFileInput.value = null;
  fileBridgeFiles.value = [];
  fileBridgePath.value = '';
  downloadableFiles.value = [];
  fileBridgeBusy.value = false;
  fileBridgeDragging.value = false;
  fileBridgeStatus.value = '';
  focusCanvas();
};

const sendFileBridgeSelection = async () => {
  const files = fileBridgeFiles.value;
  if (!pendingFileInput.value || !screencastWs || screencastWs.readyState !== WebSocket.OPEN) return;
  if (files.length === 0) {
    openFileBridgePicker();
    return;
  }
  const request = pendingFileInput.value;
  fileBridgeBusy.value = true;
  fileBridgeStatus.value = '正在设置上传文件...';
  try {
    const payloadFiles = await Promise.all(files.map(readFileAsBase64));
    screencastWs.send(JSON.stringify({
      type: 'file_upload_selection',
      token: request.token,
      source_mode: 'fixed',
      files: payloadFiles,
    }));
  } catch (err: any) {
    fileBridgeBusy.value = false;
    fileBridgeStatus.value = err?.message || '读取文件失败';
  }
};

const sendFileBridgePath = () => {
  if (!pendingFileInput.value || !screencastWs || screencastWs.readyState !== WebSocket.OPEN) return;
  const path = fileBridgePath.value.trim();
  if (!path) {
    fileBridgeStatus.value = '请输入本机文件路径';
    return;
  }
  fileBridgeBusy.value = true;
  fileBridgeStatus.value = '正在按路径设置上传文件...';
  try {
    screencastWs.send(JSON.stringify({
      type: 'file_upload_selection',
      token: pendingFileInput.value.token,
      source_mode: 'path',
      path,
    }));
  } catch (err: any) {
    fileBridgeBusy.value = false;
    fileBridgeStatus.value = err?.message || '设置上传文件失败';
  }
};

const pickDownloadable = (downloadable: DownloadableFile) => {
  if (!pendingFileInput.value || !screencastWs || screencastWs.readyState !== WebSocket.OPEN) return;
  if (!downloadable.path) {
    fileBridgeStatus.value = '这个下载记录没有可用路径';
    return;
  }
  fileBridgeBusy.value = true;
  fileBridgeStatus.value = `正在使用下载文件 ${downloadable.filename}...`;
  try {
    screencastWs.send(JSON.stringify({
      type: 'file_upload_selection',
      token: pendingFileInput.value.token,
      source_mode: 'dataflow',
      path: downloadable.path,
      upload_source: {
        mode: 'dataflow',
        source_step_id: downloadable.step_id,
        source_result_key: downloadable.result_key,
        file_field: 'path',
        original_filename: downloadable.filename,
      },
    }));
  } catch (err: any) {
    fileBridgeBusy.value = false;
    fileBridgeStatus.value = err?.message || '设置上传文件失败';
  }
};

const downloadableKindLabel = (downloadable: DownloadableFile) => (
  downloadable.kind === 'transform' ? '转换结果' : '下载文件'
);

const sendInputEvent = (e: Event) => {
  if (!screencastWs || screencastWs.readyState !== WebSocket.OPEN) return;
  const canvas = canvasRef.value;
  if (!canvas) return;

  if (e instanceof MouseEvent && !(e instanceof WheelEvent)) {
    if (e.type === 'mousemove') {
      const now = Date.now();
      if (now - lastMoveTime < MOVE_THROTTLE) return;
      lastMoveTime = now;
    }

    const rect = canvas.getBoundingClientRect();
    const point = mapClientPointToViewportPoint({
      clientX: e.clientX,
      clientY: e.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    });
    if (!point) return;
    const actionMap: Record<string, string> = {
      mousedown: 'mousePressed',
      mouseup: 'mouseReleased',
      mousemove: 'mouseMoved',
    };
    const action = actionMap[e.type];
    if (!action) return;
    const buttonMap = ['left', 'middle', 'right'];
    screencastWs.send(JSON.stringify({
      type: 'mouse',
      action,
      coordinateSpace: 'css-pixel',
      x: point.x,
      y: point.y,
      button: buttonMap[e.button] || 'left',
      clickCount: e.type === 'mousedown' || e.type === 'mouseup' ? 1 : 0,
      modifiers: getModifiers(e),
    }));
  } else if (e instanceof WheelEvent) {
    const rect = canvas.getBoundingClientRect();
    const point = mapClientPointToViewportPoint({
      clientX: e.clientX,
      clientY: e.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    });
    if (!point) return;
    screencastWs.send(JSON.stringify({
      type: 'wheel',
      coordinateSpace: 'css-pixel',
      x: point.x,
      y: point.y,
      deltaX: e.deltaX,
      deltaY: e.deltaY,
      modifiers: getModifiers(e),
    }));
  } else if (e instanceof KeyboardEvent) {
    const action = e.type === 'keydown' ? 'keyDown' : 'keyUp';
    screencastWs.send(JSON.stringify({
      type: 'keyboard',
      action,
      key: e.key,
      code: e.code,
      text: e.type === 'keydown' && e.key.length === 1 ? e.key : '',
      modifiers: getModifiers(e),
    }));
  }
};

const stopRecording = async () => {
  isRecording.value = false;
  if (timerInterval.value) clearInterval(timerInterval.value);
  if (pollInterval) clearInterval(pollInterval);

  if (sessionId.value) {
    try {
      await apiClient.post(`/rpa/session/${sessionId.value}/stop`);
    } catch (err) {
      console.error('Failed to stop session:', err);
    }
  }
  if (launchSource.value === 'mcp-tool-studio') {
    router.push(buildRpaToolEditorLocation({
      sessionId: sessionId.value || '',
    }));
    return;
  }
  router.push(`/rpa/configure?sessionId=${sessionId.value}`);
};

const goToHome = () => {
  router.push('/chat');
};

const goToSkills = () => {
  router.push('/chat/skills');
};

const deleteStep = async (step: any, fallbackStepIndex: number) => {
  if (!sessionId.value) return;
  try {
    if (step.stepId) {
      await apiClient.delete(`/rpa/session/${sessionId.value}/timeline-item`, {
        data: { kind: 'manual_step', step_id: step.stepId },
      });
    } else if (step.traceId) {
      await apiClient.delete(`/rpa/session/${sessionId.value}/timeline-item`, {
        data: { kind: 'trace', trace_id: step.traceId },
      });
    } else {
      await apiClient.delete(`/rpa/session/${sessionId.value}/step/${fallbackStepIndex}`);
    }
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    refreshTimeline(resp.data.session || {});
  } catch (err) {
    console.error('Failed to delete step:', err);
  }
};

const abortAgent = async () => {
  if (!sessionId.value) return;
  await apiClient.post(`/rpa/session/${sessionId.value}/agent/abort`);
};

const sendConfirm = async (approved: boolean) => {
  pendingConfirm.value = null;
  if (!sessionId.value) return;
  await apiClient.post(`/rpa/session/${sessionId.value}/agent/confirm`, { approved });
};

const sendMessage = async () => {
  if (!newMessage.value.trim() || !sessionId.value || sending.value) return;
  const userText = newMessage.value.trim();
  newMessage.value = '';
  sending.value = true;
  agentRunning.value = true;
  const initialProgress = getInitialRpaAgentProgress();

  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  chatMessages.value.push({ role: 'user', text: userText, time: now });

  const assistantMsg: ChatMessage = {
    role: 'assistant',
    text: '',
    time: now,
    status: initialProgress.status,
    processingLabel: initialProgress.label,
    run: createRpaAssistantRun(now),
  };
  chatMessages.value.push(assistantMsg);
  const msgIdx = chatMessages.value.length - 1;
  scrollAssistantToBottom();

  try {
    const attachmentStagingId = chatAttachment.value?.staging_id || null;
    const resp = await fetch(`/api/v1/rpa/session/${sessionId.value}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
      },
      body: JSON.stringify({
        message: userText,
        mode: 'trace_first',
        attachment_staging_id: attachmentStagingId,
      }),
    });
    if (attachmentStagingId) {
      chatAttachment.value = null;
    }

    if (!resp.ok || !resp.body) {
      chatMessages.value[msgIdx].text = '请求失败，请重试。';
      chatMessages.value[msgIdx].status = 'error';
      chatMessages.value[msgIdx].processingLabel = '';
      sending.value = false;
      agentRunning.value = false;
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let eventType = '';
      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          const raw = line.slice(5).trim();
          if (!raw) continue;
          try {
            const data = JSON.parse(raw);
            const progress = getRpaAgentProgressForEvent(eventType);
            if (progress) {
              chatMessages.value[msgIdx].status = progress.status;
              chatMessages.value[msgIdx].processingLabel = progress.label;
            }
            if (chatMessages.value[msgIdx].run) {
              chatMessages.value[msgIdx].run = applyRpaAssistantRunEvent(
                chatMessages.value[msgIdx].run!,
                eventType,
                data,
              );
            }
            if (eventType === 'message_chunk') {
              chatMessages.value[msgIdx].text += data.text || '';
            } else if (eventType === 'script') {
              chatMessages.value[msgIdx].script = data.code || '';
              chatMessages.value[msgIdx].text = cleanupAssistantText(
                chatMessages.value[msgIdx].text,
                data.code || '',
              );
            } else if (eventType === 'resolution') {
              const resolved = data.intent?.resolved || {};
              chatMessages.value[msgIdx].frameSummary = (resolved.frame_path || []).length
                ? (resolved.frame_path || []).join(' -> ')
                : 'Main frame';
              chatMessages.value[msgIdx].locatorSummary = resolved.selected_locator_kind || resolved.locator?.method || '';
              if (resolved.collection_hint?.kind) {
                chatMessages.value[msgIdx].collectionSummary = `${resolved.collection_hint.kind}${resolved.ordinal ? ` / ${resolved.ordinal}` : ''}`;
              }
            } else if (eventType === 'executing') {
              chatMessages.value[msgIdx].status = 'executing';
              chatMessages.value[msgIdx].processingLabel = '正在执行浏览器操作...';
              if (!chatMessages.value[msgIdx].text.trim()) {
                chatMessages.value[msgIdx].text = '代码已生成，正在执行浏览器操作。';
              }
            } else if (eventType === 'result') {
              chatMessages.value[msgIdx].status = data.success ? 'done' : 'error';
              chatMessages.value[msgIdx].processingLabel = '';
              if (data.error) chatMessages.value[msgIdx].error = data.error;
              if (data.output && data.output !== 'ok' && data.output !== 'None') {
                chatMessages.value[msgIdx].text += `${chatMessages.value[msgIdx].text ? '\n' : ''}输出: ${data.output}`;
              }
            } else if (eventType === 'agent_thought') {
              chatMessages.value[msgIdx].text += (chatMessages.value[msgIdx].text ? '\n' : '') + `💭 ${data.text || ''}`;
            } else if (eventType === 'agent_action') {
              const actionIdx = (chatMessages.value[msgIdx].actions?.length || 0);
              chatMessages.value[msgIdx].text += `\n⚡ ${data.description || ''} [[CODE_${actionIdx}]]`;
              if (!chatMessages.value[msgIdx].actions) chatMessages.value[msgIdx].actions = [];
              chatMessages.value[msgIdx].actions!.push({ description: data.description || '', code: data.code || '', showCode: false });
            } else if (eventType === 'agent_step_done') {
              if (data.trace) {
                acceptedTraces.value = [...acceptedTraces.value.filter((t: any) => t.trace_id !== data.trace.trace_id), data.trace];
                steps.value = mapServerTraces(acceptedTraces.value);
              }
              if (data.step) {
                const s = data.step;
                steps.value.push({
                  id: String(steps.value.length),
                  title: s.description || s.action,
                  description: s.prompt || s.description || 'AI 操作',
                  status: 'completed',
                  source: 'ai',
                  sensitive: s.sensitive || false,
                });
              }
              // Show output if present
              if (data.output) {
                const outputText = typeof data.output === 'string' ? data.output : JSON.stringify(data.output);
                chatMessages.value[msgIdx].text += `\nOutput: ${outputText}`;
              }
            } else if (eventType === 'trace_added') {
              if (data?.trace_id) {
                acceptedTraces.value = [...acceptedTraces.value.filter((t: any) => t.trace_id !== data.trace_id), data];
                steps.value = mapServerTraces(acceptedTraces.value);
              }
            } else if (eventType === 'confirm_required') {
              pendingConfirm.value = data;
            } else if (eventType === 'agent_done') {
              chatMessages.value[msgIdx].status = 'done';
              const completedCount = data.trace_count ?? data.total_steps ?? Math.max(steps.value.length - 1, 0);
              chatMessages.value[msgIdx].text += `\nTask completed, accepted ${completedCount} trace(s).`;
              agentRunning.value = false;
              pendingConfirm.value = null;
            } else if (eventType === 'agent_aborted') {
              chatMessages.value[msgIdx].status = 'error';
              chatMessages.value[msgIdx].text += `\n⚠️ Agent 已停止：${data.reason || ''}`;
              chatMessages.value[msgIdx].diagnostics = formatAgentDiagnostics(data.diagnostics || []);
              agentRunning.value = false;
              pendingConfirm.value = null;
            } else if (eventType === 'error') {
              chatMessages.value[msgIdx].status = 'error';
              chatMessages.value[msgIdx].error = data.message || '未知错误';
              agentRunning.value = false;
            }
            scrollAssistantToBottom();
          } catch { /* ignore parse errors */ }
          eventType = '';
        }
      }
    }

    if (!chatMessages.value[msgIdx].status || chatMessages.value[msgIdx].status === 'streaming') {
      chatMessages.value[msgIdx].status = 'done';
    }
  } catch (err: any) {
    chatMessages.value[msgIdx].text = `连接失败: ${err.message}`;
    chatMessages.value[msgIdx].status = 'error';
    chatMessages.value[msgIdx].processingLabel = '';
    agentRunning.value = false;
  } finally {
    sending.value = false;
  }
};
</script>

<template>
  <div class="flex flex-col h-screen bg-[#f5f6f7] dark:bg-[#161618] overflow-hidden">
    <!-- Header -->
    <RpaFlowGuide
      current-step="record"
      :session-id="sessionId"
      :recorded-step-count="Math.max(steps.length - 1, 0)"
      :diagnostic-count="recordingDiagnostics.length"
      :is-recording="isRecording"
      :recording-time="recordingTime"
      primary-label="完成录制"
      @home="goToHome"
      @skills="goToSkills"
      @go-configure="stopRecording"
      @primary-action="stopRecording"
    />

    <!-- Main Content -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left Sidebar: Steps -->
      <aside class="flex w-80 flex-shrink-0 overflow-hidden bg-[#eff1f2] dark:bg-[#212122]">
        <RpaStepTimeline
          :steps="steps"
          mode="record"
          :is-recording="isRecording"
          :auto-scroll="true"
          :active-index="isRecording && steps.length ? steps.length - 1 : null"
          :diagnostics-count="recordingDiagnostics.length"
          diagnostics-message="这些步骤不会进入 accepted timeline，完成录制后需要在配置页修复或删除。"
          show-delete
          empty-message="在浏览器中操作后，步骤会自动出现在这里。"
          @delete-step="deleteStep($event.step, $event.index - 1)"
        />
      </aside>

      <!-- Center: Screencast Viewport -->
      <main class="flex-1 bg-[#f5f6f7] dark:bg-[#161618] px-5 py-4 flex flex-col min-w-0">
        <div class="flex-1 bg-[#1e1e1e] rounded-2xl shadow-2xl relative overflow-hidden flex flex-col border border-gray-800 dark:border-gray-600">
          <div class="h-9 bg-[#cfd3d8] dark:bg-[#2a2a2b] flex items-end px-3 gap-2 flex-shrink-0 overflow-x-auto">
            <button
              v-for="tab in tabs"
              :key="tab.tab_id"
              type="button"
              @click="activateTab(tab.tab_id)"
              class="max-w-[220px] min-w-[120px] h-7 px-3 rounded-t-xl text-[11px] border border-b-0 transition-colors truncate"
              :class="tab.active ? 'bg-[#f5f6f7] dark:bg-[#161618] text-gray-900 dark:text-gray-100 border-gray-300 dark:border-gray-600' : 'bg-white/60 dark:bg-white/10 text-gray-600 dark:text-gray-400 border-transparent hover:bg-white/80 dark:hover:bg-white/20'"
            >
              {{ tab.title || tab.url || 'New Tab' }}
            </button>
          </div>
          <div class="h-9 bg-[#dadddf] dark:bg-[#383839] flex items-center px-3 gap-2 flex-shrink-0 border-t border-white/40 dark:border-white/10">
            <div class="flex gap-1.5">
              <div class="w-2.5 h-2.5 rounded-full bg-red-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-yellow-400"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-green-400"></div>
            </div>
            <form class="flex-1 bg-white dark:bg-[#272728] rounded-md h-5 mx-3 flex items-center px-2 shadow-inner border border-transparent focus-within:border-[#831bd7]/30" @submit.prevent="submitAddressBar">
              <Globe class="text-gray-400 dark:text-gray-500 flex-shrink-0" :size="12" />
              <input
                v-model="addressInput"
                class="flex-1 bg-transparent text-[10px] text-gray-700 dark:text-gray-300 ml-2 outline-none placeholder:text-gray-400"
                :disabled="!sessionId || isNavigating"
                placeholder="输入网址并按回车跳转"
                type="text"
                spellcheck="false"
                @focus="handleAddressFocus"
                @mouseup="handleAddressMouseUp"
                @blur="handleAddressBlur"
              />
              <span v-if="isNavigating" class="text-[9px] text-[#831bd7] font-medium flex-shrink-0">打开中...</span>
            </form>
          </div>

          <div class="flex-1 relative bg-black overflow-hidden">
            <canvas
              v-if="sessionId"
              ref="canvasRef"
              class="w-full h-full object-contain cursor-default"
              tabindex="0"
              @click="focusCanvas"
              @mousedown="sendInputEvent"
              @mouseup="sendInputEvent"
              @mousemove="sendInputEvent"
              @wheel.prevent="sendInputEvent"
              @keydown.prevent="sendInputEvent"
              @keyup.prevent="sendInputEvent"
              @contextmenu.prevent
            />
            <div v-else class="absolute inset-0 flex items-center justify-center flex-col gap-4 text-white/50">
              <Terminal :size="64" class="opacity-20" />
              <p v-if="loading" class="text-sm font-medium">正在启动 Playwright 浏览器...</p>
              <p v-else-if="error" class="text-sm font-medium text-red-400">{{ error }}</p>
            </div>

            <div v-if="error && sessionId" class="absolute top-4 right-4 max-w-xs bg-red-500/90 text-white text-[11px] px-3 py-2 rounded-lg shadow-lg">
              {{ error }}
            </div>
            <div
              v-if="pendingFileInput"
              class="absolute inset-0 z-30 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm"
            >
              <div class="flex w-full max-w-xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white text-slate-900 shadow-2xl dark:border-slate-800 dark:bg-[#272728] dark:text-gray-100">
                <div class="flex items-center justify-between border-b border-slate-100 bg-white px-6 py-4 dark:border-slate-800 dark:bg-[#272728]">
                  <div>
                    <h2 class="text-lg font-bold leading-6">上传内容</h2>
                    <p class="mt-1 text-xs text-slate-500 dark:text-gray-400">页面正在请求文件选择器，请选择录制技能要使用的上传来源。</p>
                  </div>
                  <button
                    type="button"
                    class="rounded-full p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-white/10 dark:hover:text-gray-100"
                    title="关闭"
                    @click="cancelFileBridge"
                  >
                    <X :size="18" />
                  </button>
                </div>

                <div class="space-y-5 p-6">
                  <div class="rounded-xl bg-slate-100 p-1 dark:bg-[#1f1f20]">
                    <div
                      class="grid gap-1"
                      :class="downloadableFiles.length > 0 ? 'grid-cols-3' : 'grid-cols-2'"
                    >
                      <button
                        type="button"
                        class="inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold transition-all"
                        :class="fileBridgeMode === 'fixed' ? 'bg-white text-[#831bd7] shadow-sm dark:bg-[#383739]' : 'text-slate-500 hover:text-slate-700 dark:text-gray-400 dark:hover:text-gray-200'"
                        @click="fileBridgeMode = 'fixed'"
                      >
                        <FileUp :size="16" />
                        固定文件
                      </button>
                      <button
                        type="button"
                        class="inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold transition-all"
                        :class="fileBridgeMode === 'path' ? 'bg-white text-[#831bd7] shadow-sm dark:bg-[#383739]' : 'text-slate-500 hover:text-slate-700 dark:text-gray-400 dark:hover:text-gray-200'"
                        @click="fileBridgeMode = 'path'"
                      >
                        <Workflow :size="16" />
                        指定路径
                      </button>
                      <button
                        v-if="downloadableFiles.length > 0"
                        type="button"
                        class="inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold transition-all"
                        :class="fileBridgeMode === 'download' ? 'bg-white text-[#831bd7] shadow-sm dark:bg-[#383739]' : 'text-slate-500 hover:text-slate-700 dark:text-gray-400 dark:hover:text-gray-200'"
                        @click="fileBridgeMode = 'download'"
                      >
                        <FileDown :size="16" />
                        从本次下载
                      </button>
                    </div>
                  </div>

                  <div v-if="fileBridgeMode === 'fixed'" class="space-y-3">
                    <button
                      type="button"
                      class="group flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed bg-slate-50/80 px-6 py-8 text-center transition-all dark:bg-[#1f1f20]/70"
                      :class="fileBridgeDragging ? 'border-[#831bd7] bg-purple-50/80 dark:bg-purple-950/20' : 'border-slate-200 hover:border-[#831bd7]/60 hover:bg-purple-50/40 dark:border-slate-700'"
                      @click="openFileBridgePicker"
                      @dragenter.prevent="fileBridgeDragging = true"
                      @dragover.prevent="fileBridgeDragging = true"
                      @dragleave.prevent="fileBridgeDragging = false"
                      @drop.prevent="handleFileBridgeDrop"
                    >
                      <CloudUpload class="mb-3 text-[#831bd7] transition-transform group-hover:scale-110" :size="34" />
                      <p class="text-sm font-semibold text-slate-700 dark:text-gray-200">点击选择或拖拽文件到这里</p>
                      <p class="mt-1 text-[11px] text-slate-500 dark:text-gray-400">
                        {{ pendingFileInput.multiple ? '支持多文件' : '单文件上传' }}{{ pendingFileInput.accept ? ` · ${pendingFileInput.accept}` : '' }}
                      </p>
                    </button>

                    <div v-if="fileBridgeFiles.length" class="space-y-2 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-[#1f1f20]">
                      <div
                        v-for="file in fileBridgeFiles"
                        :key="`${file.name}-${file.size}-${file.lastModified}`"
                        class="flex min-w-0 items-center gap-3"
                      >
                        <div class="flex size-8 flex-shrink-0 items-center justify-center rounded-lg bg-purple-50 text-[#831bd7] dark:bg-purple-950/30">
                          <FileText :size="16" />
                        </div>
                        <div class="min-w-0 flex-1">
                          <p class="truncate text-xs font-semibold text-slate-700 dark:text-gray-200">{{ file.name }}</p>
                          <p class="text-[11px] text-slate-500 dark:text-gray-400">{{ (file.size / 1024 / 1024).toFixed(2) }} MB</p>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div v-else-if="fileBridgeMode === 'download'" class="space-y-2">
                    <button
                      v-for="downloadable in downloadableFiles"
                      :key="downloadable.step_id"
                      type="button"
                      class="flex w-full min-w-0 items-center gap-3 rounded-xl border border-teal-100 bg-teal-50/70 px-3 py-3 text-left transition-colors hover:border-teal-300 hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-teal-900/60 dark:bg-teal-950/20 dark:hover:bg-teal-950/30"
                      :disabled="fileBridgeBusy || !downloadable.path"
                      @click="pickDownloadable(downloadable)"
                    >
                      <span class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white text-teal-700 dark:bg-[#272728] dark:text-teal-300">
                        <FileDown :size="16" />
                      </span>
                      <span class="min-w-0 flex-1">
                        <span class="block truncate text-xs font-semibold text-slate-700 dark:text-gray-200">{{ downloadable.filename }}</span>
                        <span class="mt-0.5 block truncate text-[11px] text-slate-500 dark:text-gray-400">
                          {{ downloadableKindLabel(downloadable) }} · {{ downloadable.path || '路径不可用' }}
                        </span>
                      </span>
                      <span v-if="downloadable.size" class="shrink-0 text-[11px] text-slate-500 dark:text-gray-400">{{ (downloadable.size / 1024).toFixed(1) }} KB</span>
                    </button>
                    <p v-if="!downloadableFiles.length" class="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-[#1f1f20] dark:text-gray-400">
                      本次会话还没有下载记录
                    </p>
                  </div>

                  <div v-else class="space-y-3">
                    <label class="grid gap-2">
                      <span class="text-xs font-semibold text-slate-600 dark:text-gray-300">本机文件路径</span>
                      <input
                        v-model="fileBridgePath"
                        class="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-[#831bd7] focus:bg-white dark:border-slate-700 dark:bg-[#1f1f20] dark:focus:bg-[#272728]"
                        placeholder="/Users/gao/Desktop/采购明细导入模板.xlsx"
                        :disabled="fileBridgeBusy"
                        @keydown.enter.prevent="sendFileBridgePath"
                      />
                    </label>
                    <p class="rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-500 dark:bg-[#1f1f20] dark:text-gray-400">
                      回放时会继续读取这个路径，不会把文件打包进技能。相对路径按工作区解析。
                    </p>
                  </div>

                  <p v-if="fileBridgeStatus" class="rounded-lg bg-slate-100 px-3 py-2 text-xs font-medium text-slate-700 dark:bg-[#1f1f20] dark:text-gray-300">
                    {{ fileBridgeStatus }}
                  </p>
                </div>

                <div class="flex items-center justify-end gap-3 border-t border-slate-200 bg-slate-50 px-6 py-4 dark:border-slate-800 dark:bg-[#1f1f20]/80">
                  <button
                    type="button"
                    class="rounded-lg px-4 py-2 text-sm font-semibold text-slate-600 transition-colors hover:bg-slate-200 dark:text-gray-300 dark:hover:bg-white/10"
                    :disabled="fileBridgeBusy"
                    @click="cancelFileBridge"
                  >
                    取消
                  </button>
                  <button
                    v-if="fileBridgeMode !== 'download'"
                    type="button"
                    class="inline-flex items-center gap-2 rounded-lg bg-[#831bd7] px-5 py-2 text-sm font-bold text-white shadow-lg shadow-purple-900/10 transition-all hover:opacity-90 active:scale-95 disabled:cursor-not-allowed disabled:opacity-60"
                    :disabled="fileBridgeBusy || (fileBridgeMode === 'fixed' && fileBridgeFiles.length === 0) || (fileBridgeMode === 'path' && !fileBridgePath.trim())"
                    @click="fileBridgeMode === 'path' ? sendFileBridgePath() : sendFileBridgeSelection()"
                  >
                    <Loader2 v-if="fileBridgeBusy" class="animate-spin" :size="16" />
                    <span>{{ fileBridgeMode === 'path' ? '使用路径' : '开始上传' }}</span>
                    <ArrowRight v-if="!fileBridgeBusy" :size="16" />
                  </button>
                </div>
              </div>
            </div>
            <input
              ref="fileBridgeInputRef"
              class="hidden"
              type="file"
              :accept="pendingFileInput?.accept || undefined"
              :multiple="!!pendingFileInput?.multiple"
              @change="handleFileBridgeInputChange"
            />
            <div v-if="fileBridgeStatus && !pendingFileInput" class="absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded-full bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-lg">
              {{ fileBridgeStatus }}
            </div>
            <div v-if="sessionId" class="absolute bottom-3 left-1/2 -translate-x-1/2 bg-white/10 backdrop-blur-md border border-white/20 px-3 py-1.5 rounded-full flex items-center gap-2">
              <Radio class="text-red-400 animate-pulse" :size="14" />
              <span class="text-white text-[10px] font-bold tracking-wider uppercase">实时 CDP 串流</span>
            </div>
          </div>
        </div>

        <div v-if="false" class="mt-6 flex justify-center gap-4">
          <button class="flex items-center gap-2 bg-white dark:bg-[#272728] px-5 py-2.5 rounded-xl text-xs font-bold text-gray-700 dark:text-gray-300 shadow-sm hover:bg-gray-50 dark:hover:bg-[#444345] transition-colors border border-gray-200 dark:border-gray-700">
            <Camera :size="16" class="text-gray-500 dark:text-gray-400" />
            截图标记
          </button>
        </div>
      </main>

      <!-- Right Sidebar: AI Chat -->
      <aside class="w-80 bg-[#eff1f2] dark:bg-[#212122] border-l border-gray-200 dark:border-gray-700 flex flex-col shadow-[-10px_0_40px_-10px_rgba(0,0,0,0.03)]">
        <div class="p-6 border-b border-gray-100 dark:border-gray-800 bg-[#eff1f2] dark:bg-[#212122]">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-[#831bd7] to-[#ac0089] flex items-center justify-center text-white shadow-lg shadow-purple-200">
                <Wand2 :size="20" />
              </div>
              <div>
                <h3 class="text-gray-900 dark:text-gray-100 font-bold text-sm">AI 录制助手</h3>
                <p
                  class="text-[10px] font-bold"
                  :class="agentRunning ? 'text-orange-500' : 'text-[#831bd7]'"
                  v-text="agentRunning ? '正在处理你的操作...' : '按描述录制操作'"
                ></p>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <button
                v-if="agentRunning"
                @click="abortAgent"
                class="text-[10px] font-bold text-red-500 border border-red-200 dark:border-red-800 px-2 py-1 rounded-lg hover:bg-red-50 transition-colors"
              >中止</button>
            </div>
          </div>
        </div>

        <div ref="chatScrollRef" class="flex-1 overflow-y-auto p-6 space-y-6 bg-[#eff1f2] dark:bg-[#212122]">
          <div v-if="chatMessages.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-xs mt-8">
            在 VNC 中操作浏览器，步骤会自动记录到左侧面板。
          </div>
          <div
            v-for="(msg, idx) in chatMessages"
            :key="idx"
            class="flex flex-col gap-1.5"
            :class="msg.role === 'user' ? 'items-end' : 'items-start'"
          >
            <div
              v-if="msg.role === 'user'"
              class="max-w-[85%] min-w-0 overflow-hidden rounded-2xl rounded-tr-none bg-[#831bd7] p-3 text-xs leading-relaxed text-white shadow-md shadow-purple-100 break-words [overflow-wrap:anywhere]"
            >
              <div class="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{{ msg.text }}</div>
            </div>

            <div
              v-else-if="msg.run"
              class="w-full max-w-[94%] overflow-hidden rounded-lg bg-white p-3 text-xs text-gray-800 shadow-[0_16px_36px_rgba(25,28,30,0.06)] dark:bg-[#272728] dark:text-gray-200"
            >
              <div class="flex min-w-0 items-start justify-between gap-2">
                <div class="flex min-w-0 items-center gap-2">
                  <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-[#831bd7] to-[#ac0089] text-white">
                    <Wand2 :size="16" />
                  </div>
                  <div class="min-w-0">
                    <div class="truncate text-[12px] font-bold text-gray-950 dark:text-gray-100">任务处理进度</div>
                    <div class="mt-0.5 flex flex-wrap gap-1 text-[9px] font-semibold text-gray-500 dark:text-gray-400">
                      <span>{{ runRoundCount(msg.run) }} 次尝试</span>
                      <span>·</span>
                      <span>已记录 {{ getRunTraceCount(msg) }} 步</span>
                      <span>·</span>
                      <span>{{ msg.time }}</span>
                    </div>
                  </div>
                </div>
                <span class="shrink-0 rounded px-2 py-1 text-[9px] font-bold" :class="getRunStatusClass(msg)">
                  {{ getRunStatusLabel(msg) }}
                </span>
              </div>

              <div v-if="msg.run.rounds.length === 0" class="mt-3 rounded-lg bg-[#f2f4f6] p-3 dark:bg-white/[0.08]">
                <div class="flex items-center gap-2 text-[11px] font-semibold text-[#831bd7] dark:text-purple-200">
                  <Loader2 :size="13" class="animate-spin" />
                  <span>{{ msg.processingLabel || 'Agent 正在规划录制步骤...' }}</span>
                </div>
              </div>

              <div v-else class="mt-3 space-y-2">
                <section
                  v-for="round in msg.run.rounds"
                  :key="round.id"
                  class="rounded-lg bg-[#f8f9fb] p-2.5 dark:bg-white/[0.06]"
                >
                  <div class="mb-2 flex items-center justify-between gap-2">
                    <div class="text-[10px] font-bold text-gray-900 dark:text-gray-100">第 {{ round.index }} 次尝试</div>
                    <span class="rounded px-1.5 py-0.5 text-[9px] font-bold" :class="getRoundStatusClass(round)">
                      {{ getRoundStatusLabel(round) }}
                    </span>
                  </div>

                  <div class="space-y-1.5">
                    <div
                      v-for="item in round.items"
                      :key="item.id"
                      class="min-w-0 rounded-md p-2"
                      :class="getRunItemToneClass(item)"
                    >
                      <div class="flex min-w-0 gap-2">
                        <div class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded bg-white/70 dark:bg-black/20">
                          <Bot v-if="item.kind === 'plan'" :size="12" />
                          <Terminal v-else-if="item.kind === 'action'" :size="12" />
                          <ClipboardCheck v-else-if="item.kind === 'trace'" :size="12" />
                          <AlertCircle v-else-if="item.kind === 'diagnostic'" :size="12" />
                          <CheckCircle v-else :size="12" />
                        </div>
                        <div class="min-w-0 flex-1">
                          <div class="break-words text-[11px] font-bold leading-snug [overflow-wrap:anywhere]">{{ item.title }}</div>
                          <div v-if="item.detail" class="mt-1 whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed opacity-85 [overflow-wrap:anywhere]">{{ item.detail }}</div>
                          <button
                            v-if="item.code"
                            @click="item.showCode = !item.showCode"
                            class="mt-2 inline-flex items-center gap-1 rounded bg-white/70 px-2 py-1 text-[10px] font-bold text-[#831bd7] transition hover:bg-white dark:bg-black/20 dark:text-purple-200"
                          >
                            <Code :size="11" />
                            {{ item.showCode ? '收起技术细节' : '查看技术细节' }}
                            <ChevronUp v-if="item.showCode" :size="11" />
                            <ChevronDown v-else :size="11" />
                          </button>
                          <pre v-if="item.code && item.showCode" class="mt-2 max-h-40 overflow-auto rounded-md bg-[#101828] p-2 text-[10px] leading-relaxed text-emerald-200"><code>{{ item.code }}</code></pre>
                        </div>
                      </div>
                    </div>
                  </div>
                </section>
              </div>

              <div
                v-if="msg.status === 'done'"
                class="mt-3 flex items-start gap-2 rounded-lg bg-emerald-50 p-2 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
              >
                <CheckCircle :size="13" class="mt-0.5 shrink-0" />
                <span class="min-w-0 break-words">任务完成，已记录 {{ getRunTraceCount(msg) }} 个可回放步骤。</span>
              </div>

              <div
                v-if="msg.status === 'error' && (msg.run.error || msg.run.diagnostics.length)"
                class="mt-3 rounded-lg bg-red-50 p-2 text-[10px] text-red-700 dark:bg-red-950/30 dark:text-red-200"
              >
                <div class="mb-1 flex items-center gap-1 font-bold">
                  <AlertCircle :size="12" />
                  <span>失败诊断</span>
                </div>
                <div v-if="msg.run.error" class="whitespace-pre-wrap break-words font-mono [overflow-wrap:anywhere]">{{ msg.run.error }}</div>
                <div v-for="(diagnostic, didx) in msg.run.diagnostics" :key="didx" class="mt-1 whitespace-pre-wrap break-words font-mono opacity-90 [overflow-wrap:anywhere]">
                  {{ didx + 1 }}. {{ diagnostic }}
                </div>
              </div>
            </div>

            <div
              v-else
              class="max-w-[85%] min-w-0 overflow-hidden rounded-2xl rounded-tl-none bg-white p-3 text-xs leading-relaxed text-gray-700 shadow-sm dark:bg-[#272728] dark:text-gray-300"
            >
              <div class="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{{ msg.text }}</div>
            </div>
            <span class="text-[9px] text-gray-400 dark:text-gray-500 font-medium px-1">{{ msg.time }}</span>
          </div>

          <!-- Inline confirm dialog for high-risk operations -->
          <div v-if="pendingConfirm" class="bg-orange-50 dark:bg-orange-900/30 border border-orange-200 dark:border-orange-800 rounded-xl p-4 text-xs">
            <p class="font-bold text-orange-700 mb-1">⚠️ 高危操作确认</p>
            <p class="text-gray-700 dark:text-gray-300 mb-1">{{ pendingConfirm.description }}</p>
            <p class="text-orange-600 text-[10px] mb-3">风险：{{ pendingConfirm.risk_reason }}</p>
            <div class="flex gap-2">
              <button @click="sendConfirm(true)" class="flex-1 bg-orange-500 text-white rounded-lg py-1.5 font-bold hover:bg-orange-600 transition-colors">确认执行</button>
              <button @click="sendConfirm(false)" class="flex-1 bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 rounded-lg py-1.5 font-bold hover:bg-gray-50 dark:hover:bg-[#444345] transition-colors">跳过</button>
            </div>
          </div>
        </div>

        <div class="p-4 bg-[#eff1f2] dark:bg-[#212122] border-t border-gray-100 dark:border-gray-800">
          <input
            ref="chatAttachmentInputRef"
            type="file"
            class="hidden"
            :accept="ALLOWED_ATTACHMENT_EXT.join(',')"
            @change="handleChatAttachmentChange"
          />
          <div v-if="chatAttachment || chatAttachmentUploading || chatAttachmentError" class="mb-2">
            <div
              v-if="chatAttachment"
              class="flex items-center gap-2 bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700 rounded-xl px-3 py-2 text-[11px]"
            >
              <FileText :size="14" class="text-[#831bd7]" />
              <div class="flex-1 min-w-0">
                <div class="truncate font-medium text-gray-700 dark:text-gray-200">{{ chatAttachment.filename }}</div>
                <div class="text-[10px] text-gray-400">模板 · {{ formatAttachmentSize(chatAttachment.size) }}</div>
              </div>
              <button
                type="button"
                @click="removeChatAttachment"
                class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                aria-label="移除模板"
              >
                <X :size="14" />
              </button>
            </div>
            <div v-else-if="chatAttachmentUploading" class="flex items-center gap-2 text-[11px] text-gray-500">
              <Loader2 :size="14" class="animate-spin" />
              <span>正在上传模板...</span>
            </div>
            <div v-else-if="chatAttachmentError" class="text-[11px] text-red-500">{{ chatAttachmentError }}</div>
          </div>
          <div class="relative">
            <input
              v-model="newMessage"
              @keyup.enter="sendMessage"
              :disabled="sending || agentRunning"
              class="w-full bg-white dark:bg-[#272728] border border-gray-200 dark:border-gray-700 rounded-2xl py-3 pl-10 pr-12 text-xs focus:ring-2 focus:ring-[#831bd7] focus:border-transparent shadow-sm placeholder:text-gray-400 outline-none disabled:opacity-50"
              :placeholder="agentRunning ? 'Agent 运行中...' : (sending ? 'AI 正在处理...' : (chatAttachment ? '描述如何按这个模板处理文件...' : '描述录制目标或操作...'))"
              type="text"
            />
            <button
              type="button"
              @click="triggerChatAttachmentPicker"
              :disabled="sending || agentRunning || chatAttachmentUploading"
              class="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-[#831bd7] transition-colors p-1.5 disabled:opacity-50"
              :title="chatAttachment ? '替换模板文件' : '附加模板文件'"
            >
              <Paperclip :size="16" />
            </button>
            <button
              @click="sendMessage"
              :disabled="sending || agentRunning"
              class="absolute right-2 top-1/2 -translate-y-1/2 text-[#831bd7] hover:scale-110 transition-transform p-1.5 disabled:opacity-50"
            >
              <Send :size="16" />
            </button>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>
