<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  Settings,
  Code,
  Tag,
  Upload,
  FileUp,
  FileDown,
  FileCog,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import RpaDiscardRecordingDialog from '@/components/rpa/RpaDiscardRecordingDialog.vue';
import RpaFlowGuide from '@/components/rpa/RpaFlowGuide.vue';
import RpaStepTimeline from '@/components/rpa/RpaStepTimeline.vue';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { buildRpaToolEditorLocation } from '@/utils/rpaMcpConvert';
import {
  getManualRecordingDiagnostics,
  getLegacyRpaSteps,
  mapRpaConfigureDisplaySteps,
  type RpaRecordingDiagnosticItem,
  type RpaConfigureStep,
} from '@/utils/rpaConfigureTimeline';
import {
  formatRpaActionLabel,
  formatRpaStepLocator,
} from '@/utils/rpaStepTimeline';

const router = useRouter();
const route = useRoute();

const sessionId = computed(() => typeof route.query.sessionId === 'string' ? route.query.sessionId : '');
const loading = ref(true);
const loadFailed = ref(false);
const error = ref<string | null>(null);

interface ParsedLocator {
  method?: string;
  role?: string;
  name?: string;
  value?: string;
  parent?: ParsedLocator;
  child?: ParsedLocator;
  base?: ParsedLocator;
  index?: number;
  locator?: ParsedLocator;
}

interface LocatorCandidate {
  kind?: string;
  score?: number;
  selected?: boolean;
  reason?: string;
  strict_match_count?: number;
  visible_match_count?: number;
  locator?: ParsedLocator | string | null;
}

interface StepValidation {
  status?: string;
  details?: string;
}

interface StepItem extends RpaConfigureStep {
  id: string;
  action: string;
  target?: ParsedLocator | string | null;
  frame_path?: string[];
  locator_candidates?: LocatorCandidate[];
  validation?: StepValidation;
  value?: string;
  description?: string;
  label?: string;
  sensitive?: boolean;
  url?: string;
  source?: string;
  configurable?: boolean;
  signals?: Record<string, any>;
  legacy_step_index?: number;
}

interface ParamItem {
  id: string;
  name: string;
  label: string;
  original_value: string;
  current_value: string;
  enabled: boolean;
  step_id: string;
  sensitive: boolean;
  credential_id: string;
  type: string;
  description?: string;
  required?: boolean;
}

interface CredentialItem {
  id: string;
  name?: string;
  username?: string;
}

interface DownloadableStep {
  step_index: number;
  step_id: string;
  filename: string;
  description: string;
  result_key: string;
  kind?: string;
  size?: number;
  path?: string;
}

const steps = ref<StepItem[]>([]);
const legacySteps = ref<StepItem[]>([]);
const diagnostics = ref<RpaRecordingDiagnosticItem[]>([]);
const skillName = ref('');
const skillDescription = ref('');
const generatedScript = ref('');
const scriptGenerating = ref(false);
const params = ref<ParamItem[]>([]);
const credentials = ref<CredentialItem[]>([]);
const downloadableSteps = ref<DownloadableStep[]>([]);
const promotingStepIndex = ref<number | null>(null);
const uploadingStepIndex = ref<number | null>(null);
const transformingStepIndex = ref<number | null>(null);
const expandedStepIndex = ref<number | null>(null);
const isScriptDrawerOpen = ref(false);
const hasDiagnostics = computed(() => diagnostics.value.length > 0);
const isDiscardDialogOpen = ref(false);

const parseLocator = (raw: unknown): ParsedLocator | null => {
  if (!raw) return null;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return { method: 'css', value: raw };
    }
  }
  return raw as ParsedLocator;
};

const VALIDATION_LABELS: Record<string, string> = {
  ok: 'Strict match',
  ambiguous: 'Ambiguous / not unique',
  fallback: 'Fallback',
  warning: 'Warning',
  broken: 'Broken',
};

const VALIDATION_CLASS_MAP: Record<string, string> = {
  ok: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400 ring-1 ring-emerald-200 dark:ring-emerald-800',
  ambiguous: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  fallback: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  warning: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 ring-1 ring-amber-200 dark:ring-amber-800',
  broken: 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-400 ring-1 ring-rose-200 dark:ring-rose-800',
};

const getValidationLabel = (status?: string) => {
  if (!status) return 'Unknown';
  return VALIDATION_LABELS[status] || status.replace(/_/g, ' ');
};

const getValidationClass = (status?: string) => {
  if (!status) return 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700';
  return VALIDATION_CLASS_MAP[status] || 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700';
};

const getActionLabel = (action: string) => {
  const map: Record<string, string> = {
    click: '点击',
    fill: '输入',
    press: '按键',
    select: '选择',
    navigate: '打开页面',
    goto: '打开页面',
    navigate_click: '点击后跳转',
    navigate_press: '按键后跳转',
    open_tab_click: '点击新标签',
    switch_tab: '切换标签',
    close_tab: '关闭标签',
    download_click: '点击下载',
    download: '下载',
    file_transform: '处理文件',
    set_input_files: '上传文件',
  };
  return map[action] || action;
};

const getActionColor = (action: string) => {
  const map: Record<string, string> = {
    click: 'bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-400',
    fill: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-400',
    press: 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400',
    select: 'bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-700 dark:text-fuchsia-400',
    navigate: 'bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-400',
    goto: 'bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-400',
    navigate_click: 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-400',
    navigate_press: 'bg-cyan-100 dark:bg-cyan-900/40 text-cyan-700 dark:text-cyan-400',
    open_tab_click: 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-400',
    switch_tab: 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300',
    close_tab: 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-400',
    download_click: 'bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-200',
    download: 'bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-200',
    file_transform: 'bg-teal-100 dark:bg-teal-900/40 text-teal-700 dark:text-teal-300',
    set_input_files: 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-400',
  };
  return map[action] || 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300';
};

const getStepFileOperation = (step: StepItem): 'upload' | 'download' | 'transform' | '' => {
  const signals = step.signals || {};
  if (step.action === 'set_input_files' || signals.set_input_files) return 'upload';
  if (step.action === 'download' || step.action === 'download_click' || signals.download) return 'download';
  if (step.action === 'file_transform' || signals.file_transform) return 'transform';
  return '';
};

const isDownloadStep = (step: StepItem) => getStepFileOperation(step) === 'download';

const downloadFilename = (step: StepItem) => {
  const download = step.signals?.download;
  if (download && typeof download === 'object') {
    const filename = download.filename || download.suggested_filename || download.original_filename;
    if (filename) return String(filename);
  }
  return getStepFileOperation(step) === 'download' && step.value ? String(step.value) : '';
};

const getStepTitle = (step: StepItem) => {
  if (step.description) return step.description;
  return `${getActionLabel(step.action)} ${formatRpaStepLocator(step.target || step.label || '')}`;
};

const isUploadStep = (step: StepItem) => step.action === 'set_input_files';

const uploadSignals = (step: StepItem) => step.signals || {};

const uploadSource = (step: StepItem) => {
  const source = uploadSignals(step).upload_source;
  return source && typeof source === 'object' ? source : {};
};

const uploadStaging = (step: StepItem) => {
  const staging = uploadSignals(step).upload_staging;
  return staging && typeof staging === 'object' ? staging : {};
};

const uploadHint = (step: StepItem) => {
  const hint = uploadSignals(step).upload_hint;
  return hint && typeof hint === 'object' ? hint : {};
};

const uploadFiles = (step: StepItem): string[] => {
  const files = uploadSignals(step).set_input_files?.files;
  if (Array.isArray(files)) return files.map((item) => String(item)).filter(Boolean);
  return step.value ? [String(step.value)] : [];
};

const uploadOriginalFilename = (step: StepItem) => {
  const staging = uploadStaging(step);
  if (staging.original_filename) return String(staging.original_filename);
  const firstFile = uploadFiles(step)[0];
  return firstFile || 'upload.bin';
};

const uploadStepIndex = (step: StepItem, displayIndex: number) => (
  typeof step.legacy_step_index === 'number' ? step.legacy_step_index : displayIndex
);

const defaultUploadParamName = (displayIndex: number) => `upload_file_${displayIndex + 1}`;

const uploadSourceGroupName = (step: StepItem, displayIndex: number) => (
  `upload-source-${uploadStepIndex(step, displayIndex)}`
);

const resetUploadSourceRadios = (event: Event | undefined, mode: string) => {
  const input = event?.target as HTMLInputElement | null;
  const groupName = input?.name;
  if (!groupName) return;
  document
    .querySelectorAll<HTMLInputElement>(`input[name="${CSS.escape(groupName)}"]`)
    .forEach((radio) => {
      radio.checked = radio.value === mode;
    });
};

const sourceModeLabel = (mode?: string) => {
  if (mode === 'parameter') return '运行时参数';
  if (mode === 'path') return '指定路径';
  if (mode === 'dataflow') return '关联产物';
  return '固定文件';
};

const getUploadBadge = (step: StepItem) => {
  if (!isUploadStep(step)) return '';
  const source = uploadSource(step);
  if (source.mode) return sourceModeLabel(source.mode);
  if (uploadStaging(step).staging_id || uploadStaging(step).items) return '未确认来源';
  return '文件缺失';
};

const getUploadBadgeClass = (step: StepItem) => {
  const source = uploadSource(step);
  if (source.mode === 'dataflow') return 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300';
  if (source.mode === 'parameter') return 'bg-sky-100 dark:bg-sky-900/40 text-sky-700 dark:text-sky-300';
  if (source.mode === 'path') return 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300';
  if (source.mode === 'fixed') return 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300';
  return 'bg-rose-100 dark:bg-rose-900/40 text-rose-700 dark:text-rose-300';
};

const promoteLocator = async (stepIndex: number, candidateIndex: number) => {
  if (!sessionId.value || promotingStepIndex.value !== null) return;
  promotingStepIndex.value = stepIndex;
  error.value = null;
  try {
    await apiClient.post(`/rpa/session/${sessionId.value}/step/${stepIndex}/locator`, {
      candidate_index: candidateIndex,
    });
    await loadSession();
  } catch (err: any) {
    error.value = `切换定位器失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    promotingStepIndex.value = null;
  }
};

const loadDownloadableSteps = async () => {
  if (!sessionId.value) return;
  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}/downloadable_steps`);
    downloadableSteps.value = resp.data.steps || [];
  } catch {
    downloadableSteps.value = [];
  }
};

const refreshAfterUploadSourceChange = async (displayIndex: number) => {
  await loadSession();
  await loadDownloadableSteps();
  expandedStepIndex.value = displayIndex;
  await generateScript({ openDrawer: false });
};

const putUploadSource = async (step: StepItem, displayIndex: number, source: Record<string, any>) => {
  if (!sessionId.value) return;
  const idx = uploadStepIndex(step, displayIndex);
  error.value = null;
  try {
    await apiClient.put(`/rpa/session/${sessionId.value}/step/${idx}/upload_source`, source);
    await refreshAfterUploadSourceChange(displayIndex);
  } catch (err: any) {
    error.value = `更新文件来源失败: ${err.response?.data?.detail || err.message}`;
  }
};

const promoteRecordedFile = async (step: StepItem, displayIndex: number) => {
  if (!sessionId.value) return;
  uploadingStepIndex.value = displayIndex;
  error.value = null;
  try {
    const idx = uploadStepIndex(step, displayIndex);
    await apiClient.post(`/rpa/session/${sessionId.value}/step/${idx}/promote_staging`);
    await refreshAfterUploadSourceChange(displayIndex);
  } catch (err: any) {
    error.value = `使用录制文件失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    uploadingStepIndex.value = null;
  }
};

const uploadFixedAsset = async (step: StepItem, displayIndex: number, event: Event) => {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file || !sessionId.value) return;
  uploadingStepIndex.value = displayIndex;
  error.value = null;
  try {
    const form = new FormData();
    form.append('file', file);
    const idx = uploadStepIndex(step, displayIndex);
    await apiClient.post(`/rpa/session/${sessionId.value}/step/${idx}/asset`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    await refreshAfterUploadSourceChange(displayIndex);
  } catch (err: any) {
    error.value = `上传文件失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    uploadingStepIndex.value = null;
  }
};

const chooseFixedSource = async (step: StepItem, displayIndex: number, event?: Event) => {
  const source = uploadSource(step);
  if (source.mode === 'fixed') return;
  const staging = uploadStaging(step);
  if (staging.staging_id || staging.items) {
    await promoteRecordedFile(step, displayIndex);
    return;
  }
  if (!source.asset_path) {
    error.value = '文件缺失，请重新上传';
    resetUploadSourceRadios(event, source.mode || 'fixed');
    return;
  }
  await putUploadSource(step, displayIndex, {
    mode: 'fixed',
    asset_path: source.asset_path || `assets/${uploadOriginalFilename(step)}`,
    original_filename: uploadOriginalFilename(step),
  });
};

const chooseParameterSource = async (step: StepItem, displayIndex: number) => {
  const source = uploadSource(step);
  await putUploadSource(step, displayIndex, {
    mode: 'parameter',
    param_name: source.param_name || defaultUploadParamName(displayIndex),
    default_asset_path: source.default_asset_path || source.asset_path || '',
    original_filename: uploadOriginalFilename(step),
    required: false,
  });
};

const choosePathSource = async (step: StepItem, displayIndex: number, event?: Event) => {
  const source = uploadSource(step);
  const existingPath = String(source.path || '').trim();
  const path = existingPath || window.prompt('请输入本机文件路径', '')?.trim() || '';
  if (!path) {
    resetUploadSourceRadios(event, source.mode || 'fixed');
    return;
  }
  await putUploadSource(step, displayIndex, {
    mode: 'path',
    path,
    original_filename: uploadOriginalFilename(step),
  });
};

const updatePathSource = async (step: StepItem, displayIndex: number, event: Event) => {
  const path = (event.target as HTMLInputElement).value.trim();
  const source = uploadSource(step);
  await putUploadSource(step, displayIndex, {
    ...source,
    mode: 'path',
    path,
    original_filename: uploadOriginalFilename(step),
  });
};

const updateParameterName = async (step: StepItem, displayIndex: number, event: Event) => {
  const name = (event.target as HTMLInputElement).value.trim();
  if (!name) return;
  const source = uploadSource(step);
  await putUploadSource(step, displayIndex, {
    ...source,
    mode: 'parameter',
    param_name: name,
    original_filename: uploadOriginalFilename(step),
  });
};

const useRecordedFileAsParameterDefault = async (step: StepItem, displayIndex: number) => {
  const source = uploadSource(step);
  await putUploadSource(step, displayIndex, {
    ...source,
    mode: 'parameter',
    param_name: source.param_name || defaultUploadParamName(displayIndex),
    default_asset_path: source.default_asset_path || `assets/${uploadOriginalFilename(step)}`,
    original_filename: uploadOriginalFilename(step),
    required: false,
  });
};

const chooseDataflowSource = async (step: StepItem, displayIndex: number, selectedKey?: string) => {
  const hint = uploadHint(step);
  const selected = downloadableSteps.value.find((item) => item.result_key === selectedKey)
    || downloadableSteps.value.find((item) => item.step_id === hint.source_step_id)
    || downloadableSteps.value[0];
  if (!selected && !hint.source_result_key) return;
  await putUploadSource(step, displayIndex, {
    mode: 'dataflow',
    source_step_id: selected?.step_id || hint.source_step_id,
    source_result_key: selected?.result_key || hint.source_result_key,
    file_field: 'path',
    original_filename: uploadOriginalFilename(step),
  });
};

const onDataflowSelect = (step: StepItem, displayIndex: number, event: Event) => {
  chooseDataflowSource(step, displayIndex, (event.target as HTMLSelectElement).value);
};

const sourceOptionLabel = (item: DownloadableStep) => {
  const kind = item.kind === 'transform' ? '转换结果' : '下载文件';
  return `第 ${item.step_index + 1} 步：${kind} · ${item.filename}`;
};

const createFileTransform = async (step: StepItem, displayIndex: number) => {
  if (!sessionId.value) return;
  const source = downloadableSteps.value.find((item) => item.step_id === step.id)
    || downloadableSteps.value.find((item) => item.result_key === step.signals?.download?.result_key);
  const instruction = window.prompt('请输入文件处理要求', '')?.trim() || '';
  if (!instruction) return;
  const defaultName = source?.filename
    ? `${source.filename.replace(/\.[^.]+$/, '')}_处理后.xlsx`
    : 'converted.xlsx';
  const outputFilename = window.prompt('输出 Excel 文件名', defaultName)?.trim() || defaultName;
  transformingStepIndex.value = displayIndex;
  error.value = null;
  try {
    await apiClient.post(`/rpa/session/${sessionId.value}/file-transform`, {
      instruction,
      source_step_id: source?.step_id || step.id,
      source_result_key: source?.result_key || step.signals?.download?.result_key,
      output_filename: outputFilename,
      auto_link_next_upload: true,
    });
    await loadSession();
    await loadDownloadableSteps();
    await generateScript({ openDrawer: false });
    const insertedIndex = steps.value.findIndex((item) => item.action === 'file_transform' && item.value === outputFilename);
    expandedStepIndex.value = insertedIndex >= 0 ? insertedIndex : displayIndex + 1;
  } catch (err: any) {
    error.value = `处理文件失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    transformingStepIndex.value = null;
  }
};

const promoteDiagnosticLocator = async (diagnostic: RpaRecordingDiagnosticItem, candidateIndex: number) => {
  if (diagnostic.stepIndex === null) return;
  await promoteLocator(diagnostic.stepIndex, candidateIndex);
};

const deleteDiagnosticStep = async (diagnostic: RpaRecordingDiagnosticItem) => {
  if (!sessionId.value || diagnostic.stepIndex === null || promotingStepIndex.value !== null) return;
  promotingStepIndex.value = diagnostic.stepIndex;
  error.value = null;
  try {
    await apiClient.delete(`/rpa/session/${sessionId.value}/step/${diagnostic.stepIndex}`);
    await loadSession();
    if (!diagnostics.value.length && !generatedScript.value) {
      await generateScript({ openDrawer: false });
    }
  } catch (err: any) {
    error.value = `删除待修复步骤失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    promotingStepIndex.value = null;
  }
};

const loadCredentials = async () => {
  try {
    const resp = await apiClient.get('/credentials');
    credentials.value = resp.data.credentials || [];
  } catch {
    // Credentials are optional for this page.
  }
};

const KEYWORD_MAP: Record<string, string> = {
  邮箱: 'email', 邮件: 'email', email: 'email', 'e-mail': 'email',
  密码: 'password', password: 'password', pwd: 'password',
  用户名: 'username', 用户: 'username', username: 'username', user: 'username',
  账号: 'account', account: 'account',
  手机: 'phone', 电话: 'phone', phone: 'phone', tel: 'phone', mobile: 'phone',
  验证码: 'captcha', captcha: 'captcha', code: 'code',
  搜索: 'search', search: 'search',
  地址: 'address', address: 'address', url: 'url',
  姓名: 'name', name: 'name',
};

function deriveParamName(loc: ParsedLocator | null, sensitive: boolean): string {
  if (!loc) return sensitive ? 'password' : '';
  if (sensitive) return 'password';

  const candidates: string[] = [];
  if (loc.name) candidates.push(loc.name);
  if (loc.value && loc.method !== 'css') candidates.push(loc.value);
  if (loc.role) candidates.push(loc.role);

  for (const text of candidates) {
    const lower = text.toLowerCase().trim();
    for (const [keyword, paramName] of Object.entries(KEYWORD_MAP)) {
      if (lower.includes(keyword)) return paramName;
    }

    const ascii = lower
      .replace(/[^a-z0-9_]/g, '_')
      .replace(/_+/g, '_')
      .replace(/^_|_$/g, '');
    if (ascii && ascii.length >= 2 && ascii.length <= 30 && /^[a-z]/.test(ascii)) {
      return ascii;
    }
  }
  return '';
}

const loadSession = async () => {
  if (!sessionId.value) {
    error.value = '缺少 sessionId 参数';
    loadFailed.value = true;
    loading.value = false;
    return;
  }

  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    const session = resp.data.session;
    legacySteps.value = getLegacyRpaSteps(session) as StepItem[];
    steps.value = mapRpaConfigureDisplaySteps(session) as StepItem[];
    diagnostics.value = getManualRecordingDiagnostics(session);
    loadFailed.value = false;
    error.value = null;

    const usedNames = new Set<string>();
    const configuredParams = legacySteps.value
      .filter((step) => step.action === 'fill' || step.action === 'select')
      .map((step, index) => {
        let label = `参数${index + 1}`;
        let semanticName = '';

        try {
          const loc = parseLocator(step.target);
          if (loc?.name) label = loc.name;
          else if (loc?.value) label = loc.value;
          semanticName = deriveParamName(loc, !!step.sensitive);
        } catch {
          // Fall back to generated defaults.
        }

        let name = semanticName || `param_${index}`;
        if (usedNames.has(name)) {
          let suffix = 2;
          while (usedNames.has(`${name}_${suffix}`)) suffix++;
          name = `${name}_${suffix}`;
        }
        usedNames.add(name);

        return {
          id: `param_${index}`,
          name,
          label,
          original_value: step.value || '',
          current_value: step.value || '',
          enabled: true,
          step_id: step.id,
          sensitive: !!step.sensitive,
          credential_id: '',
          type: 'string',
          description: label,
          required: false,
        };
      });

    legacySteps.value
      .filter((step) => step.action === 'set_input_files')
      .forEach((step, index) => {
        const source = uploadSource(step);
        if (source.mode !== 'parameter' || !source.param_name) return;
        const name = String(source.param_name);
        if (usedNames.has(name)) return;
        usedNames.add(name);
        configuredParams.push({
          id: `upload_param_${index}`,
          name,
          label: `上传文件: ${source.original_filename || uploadOriginalFilename(step)}`,
          original_value: source.default_asset_path || '',
          current_value: source.default_asset_path || '',
          enabled: true,
          step_id: step.id,
          sensitive: false,
          credential_id: '',
          type: 'file',
          description: `上传文件 ${source.original_filename || uploadOriginalFilename(step)}`,
          required: !!source.required && !source.default_asset_path,
        });
      });

    params.value = configuredParams;

    const navStep = steps.value.find((step) => !!step.url) || legacySteps.value.find((step) => !!step.url);
    if (navStep?.url) {
      try {
        const url = new URL(navStep.url);
        skillName.value = `${url.hostname} 自动化`;
      } catch {
        skillName.value = '录制技能';
      }
    } else {
      skillName.value = '录制技能';
    }
    skillDescription.value = `自动执行 ${steps.value.length} 个录制步骤`;
  } catch (err: any) {
    error.value = `加载会话失败: ${err.response?.data?.detail || err.message}`;
    if (!steps.value.length) loadFailed.value = true;
  } finally {
    loading.value = false;
  }
};

const buildParamMap = () => {
  const paramMap: Record<string, any> = {};
  params.value
    .filter((param) => param.enabled)
    .forEach((param) => {
      paramMap[param.name] = {
        type: param.type || 'string',
        description: param.description || param.label || '',
        original_value: param.type === 'file' ? param.current_value : param.original_value,
        sensitive: param.sensitive || false,
        credential_id: param.credential_id || '',
        required: !!param.required,
      };
    });
  return paramMap;
};

const generateScript = async (options: { openDrawer?: boolean } | Event = { openDrawer: true }) => {
  const resolvedOptions = options instanceof Event ? { openDrawer: true } : options;
  if (hasDiagnostics.value) {
    generatedScript.value = '';
    isScriptDrawerOpen.value = false;
    error.value = `还有 ${diagnostics.value.length} 个待修复步骤，修复后才能生成脚本`;
    return;
  }
  try {
    scriptGenerating.value = true;
    error.value = null;
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/generate`, {
      params: buildParamMap(),
    });
    generatedScript.value = resp.data.script || '';
    isScriptDrawerOpen.value = resolvedOptions.openDrawer !== false;
  } catch (err: any) {
    isScriptDrawerOpen.value = false;
    generatedScript.value = '';
    error.value = `生成脚本失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    scriptGenerating.value = false;
  }
};

const goToTest = () => {
  if (hasDiagnostics.value) {
    error.value = `还有 ${diagnostics.value.length} 个待修复步骤，修复后才能开始测试`;
    return;
  }
  router.push({
    path: '/rpa/test',
    query: {
      sessionId: sessionId.value,
      skillName: skillName.value,
      skillDescription: skillDescription.value,
      params: JSON.stringify(buildParamMap()),
    },
  });
};

const confirmDiscardAndRecord = () => {
  isDiscardDialogOpen.value = true;
};

const startNewRecording = () => {
  router.push('/rpa/recorder');
};

const goToHome = () => {
  router.push('/chat');
};

const goToSkills = () => {
  router.push('/chat/skills');
};

const goToMcpToolEditor = () => {
  router.push(buildRpaToolEditorLocation({
    sessionId: sessionId.value,
    skillName: skillName.value,
    skillDescription: skillDescription.value,
  }));
};

const handleSecondaryAction = (id: string) => {
  if (id === 'preview-script') {
    generateScript();
    return;
  }
  if (id === 'convert-mcp') {
    goToMcpToolEditor();
  }
};

onMounted(async () => {
  await loadSession();
  await loadDownloadableSteps();
  loadCredentials();
  if (!loadFailed.value && sessionId.value && !hasDiagnostics.value) {
    await generateScript({ openDrawer: false });
  }
});
</script>

<template>
  <div class="min-h-screen bg-[#f5f6f7] dark:bg-[#161618] text-gray-900 dark:text-gray-100">
    <RpaFlowGuide
      class="sticky top-0 z-30"
      current-step="configure"
      :session-id="sessionId"
      :recorded-step-count="steps.length"
      :diagnostic-count="diagnostics.length"
      :skill-name="skillName"
      primary-label="开始测试"
      :primary-disabled="hasDiagnostics"
      :secondary-actions="[
        { id: 'convert-mcp', label: '转换为 MCP 工具', tone: 'accent' },
        { id: 'preview-script', label: scriptGenerating ? '生成中...' : '预览脚本', disabled: scriptGenerating || hasDiagnostics },
      ]"
      @home="goToHome"
      @skills="goToSkills"
      @go-record="confirmDiscardAndRecord"
      @go-test="goToTest"
      @primary-action="goToTest"
      @secondary-action="handleSecondaryAction"
    />

    <RpaDiscardRecordingDialog
      v-model:open="isDiscardDialogOpen"
      @confirm="startNewRecording"
    />

    <div v-if="loading" class="flex h-64 items-center justify-center">
      <p class="text-sm text-gray-500 dark:text-gray-400">加载中...</p>
    </div>

    <div v-else-if="loadFailed" class="flex h-64 items-center justify-center px-6">
      <div class="rounded-2xl border border-rose-200 bg-white dark:bg-[#272728] px-6 py-5 text-sm text-rose-600 shadow-sm">
        {{ error || '页面加载失败' }}
      </div>
    </div>

    <main v-else class="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <div v-if="error" class="mb-4 rounded-2xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/30 px-4 py-3 text-sm text-amber-800">
        {{ error }}
      </div>

      <div class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section class="space-y-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="text-xl font-extrabold tracking-tight">录制步骤</h2>
              <p class="text-sm text-gray-500 dark:text-gray-400">默认显示摘要信息，点击任一步骤可展开查看详细定位诊断。</p>
            </div>
            <div class="rounded-full bg-white dark:bg-[#272728] px-4 py-1.5 text-xs font-bold text-[#831bd7] shadow-sm ring-1 ring-[#831bd7]/10">
              共 {{ steps.length }} 步
            </div>
          </div>

          <section
            v-if="diagnostics.length"
            class="rounded-3xl border border-rose-200 dark:border-rose-900/60 bg-rose-50/80 dark:bg-rose-950/20 p-4 shadow-sm"
          >
            <div class="flex items-start justify-between gap-3">
              <div>
                <h3 class="text-sm font-bold text-rose-700 dark:text-rose-300">待修复步骤</h3>
                <p class="mt-1 text-xs text-rose-600 dark:text-rose-300/80">
                  这些步骤还没有形成稳定可回放的事实，修复或删除后才能生成脚本。
                </p>
              </div>
              <span class="rounded-full bg-white/80 dark:bg-rose-950/40 px-3 py-1 text-[11px] font-bold text-rose-700 dark:text-rose-300 ring-1 ring-rose-200 dark:ring-rose-900/60">
                {{ diagnostics.length }} 个待处理
              </span>
            </div>

            <div class="mt-4 space-y-3">
              <article
                v-for="diagnostic in diagnostics"
                :key="diagnostic.id"
                class="rounded-2xl border border-rose-200/80 dark:border-rose-900/60 bg-white dark:bg-[#272728] p-4"
              >
                <div class="flex items-start justify-between gap-3">
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="rounded-full bg-rose-100 dark:bg-rose-900/40 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide text-rose-700 dark:text-rose-300">
                        {{ formatRpaActionLabel(diagnostic.action) }}
                      </span>
                      <span
                        class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                        :class="getValidationClass(diagnostic.validation.status)"
                      >
                        {{ getValidationLabel(diagnostic.validation.status) }}
                      </span>
                    </div>
                    <h4 class="mt-2 text-sm font-bold text-gray-900 dark:text-gray-100">
                      {{ diagnostic.description }}
                    </h4>
                    <p class="mt-1 text-xs text-rose-700 dark:text-rose-300/80">
                      {{ diagnostic.validation.details }}
                    </p>
                    <p v-if="diagnostic.url" class="mt-2 break-all font-mono text-[11px] text-gray-500 dark:text-gray-400">
                      {{ diagnostic.url }}
                    </p>
                  </div>
                  <button
                    type="button"
                    class="shrink-0 rounded-full border border-rose-200 dark:border-rose-900/60 px-3 py-1.5 text-xs font-semibold text-rose-700 dark:text-rose-300 transition-colors hover:bg-rose-100/80 dark:hover:bg-rose-900/30 disabled:cursor-not-allowed disabled:opacity-60"
                    :disabled="diagnostic.stepIndex === null || promotingStepIndex === diagnostic.stepIndex"
                    @click="deleteDiagnosticStep(diagnostic)"
                  >
                    {{ promotingStepIndex === diagnostic.stepIndex ? '处理中...' : '删除该步' }}
                  </button>
                </div>

                <div v-if="diagnostic.locator_candidates?.length" class="mt-4 space-y-2">
                  <div
                    v-for="(candidate, candidateIndex) in diagnostic.locator_candidates"
                    :key="`${diagnostic.id}-${candidateIndex}`"
                    class="flex flex-col gap-2 rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-3 md:flex-row md:items-start md:justify-between"
                  >
                    <div class="min-w-0 flex-1">
                      <div class="flex flex-wrap items-center gap-2 text-[11px]">
                        <span class="rounded-full bg-gray-100 dark:bg-[#444345] px-2 py-0.5 font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
                          {{ candidate.kind || 'locator' }}
                        </span>
                        <span v-if="candidate.playwright_locator" class="text-gray-400 dark:text-gray-500">Playwright</span>
                        <span v-if="candidate.selector" class="text-gray-400 dark:text-gray-500">Selector</span>
                      </div>
                      <p class="mt-1 break-all font-mono text-xs text-gray-700 dark:text-gray-300">
                        {{ formatRpaStepLocator(candidate.locator) }}
                      </p>
                      <p v-if="candidate.reason" class="mt-1 text-[11px] text-gray-500 dark:text-gray-400">{{ candidate.reason }}</p>
                    </div>

                    <button
                      type="button"
                      class="shrink-0 rounded-full border border-[#831bd7]/25 px-3 py-1.5 text-xs font-semibold text-[#831bd7] transition-colors hover:bg-[#831bd7]/5 disabled:cursor-not-allowed disabled:opacity-60"
                      :disabled="diagnostic.stepIndex === null || promotingStepIndex === diagnostic.stepIndex"
                      @click="promoteDiagnosticLocator(diagnostic, candidateIndex)"
                    >
                      {{ promotingStepIndex === diagnostic.stepIndex ? '切换中...' : '使用此定位器' }}
                    </button>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <RpaStepTimeline
            class="min-h-[620px] overflow-hidden rounded-2xl"
            :steps="steps"
            mode="configure"
            :show-header="false"
            :show-candidates="true"
            :promoting-step-index="promotingStepIndex"
            empty-message="当前没有可配置的录制步骤。"
            @promote-locator="promoteLocator($event.stepIndex, $event.candidateIndex)"
          />

          <section
            v-if="steps.some((step) => getStepFileOperation(step))"
            class="space-y-3 rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] p-4 shadow-sm"
          >
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 class="text-sm font-extrabold">文件流配置</h3>
                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">确认上传文件来源，或把下载结果插入文件处理后再上传。</p>
              </div>
              <span class="rounded-full bg-violet-100 px-2.5 py-1 text-[10px] font-semibold text-violet-700 dark:bg-violet-900/40 dark:text-violet-300">
                {{ steps.filter((step) => getStepFileOperation(step)).length }} 个文件步骤
              </span>
            </div>

            <template
              v-for="(step, idx) in steps"
              :key="`file-${step.id || idx}`"
            >
              <article
                v-if="getStepFileOperation(step)"
                class="space-y-3 rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] p-4"
              >
                <div class="flex flex-wrap items-start justify-between gap-3">
                  <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2">
                      <span
                        class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide"
                        :class="getActionColor(step.action)"
                      >
                        <FileUp v-if="getStepFileOperation(step) === 'upload'" :size="12" />
                        <FileDown v-else-if="getStepFileOperation(step) === 'download'" :size="12" />
                        <FileCog v-else :size="12" />
                        {{ getActionLabel(step.action) }}
                      </span>
                      <span
                        v-if="isUploadStep(step)"
                        class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                        :class="getUploadBadgeClass(step)"
                      >
                        {{ getUploadBadge(step) }}
                      </span>
                    </div>
                    <h4 class="mt-2 text-sm font-bold">{{ getStepTitle(step) }}</h4>
                    <p class="mt-1 break-all text-xs text-gray-500 dark:text-gray-400">
                      {{ isUploadStep(step) ? (uploadFiles(step).join(', ') || '未记录文件名') : (downloadFilename(step) || step.value || '文件步骤') }}
                    </p>
                  </div>

                  <button
                    v-if="isDownloadStep(step)"
                    type="button"
                    class="inline-flex items-center gap-1.5 rounded-lg bg-teal-700 px-3 py-2 text-xs font-bold text-white transition-opacity hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-60"
                    :disabled="transformingStepIndex === idx"
                    @click="createFileTransform(step, idx)"
                  >
                    <FileCog :size="14" />
                    {{ transformingStepIndex === idx ? '处理中...' : '插入文件处理' }}
                  </button>
                </div>

                <div
                  v-if="isUploadStep(step) && uploadHint(step).suggested_mode === 'dataflow' && uploadSource(step).mode !== 'dataflow'"
                  class="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/30 px-3 py-2.5 text-xs text-amber-800 dark:text-amber-200"
                >
                  <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <span>
                      检测到这个上传文件可能来自第 {{ (uploadHint(step).source_step_index ?? 0) + 1 }} 步下载的 {{ uploadHint(step).filename || uploadOriginalFilename(step) }}。
                    </span>
                    <div class="flex shrink-0 gap-2">
                      <button
                        type="button"
                        class="rounded-lg bg-amber-600 px-2.5 py-1 text-[11px] font-bold text-white"
                        @click="chooseDataflowSource(step, idx)"
                      >
                        是，关联
                      </button>
                      <button
                        type="button"
                        class="rounded-lg border border-amber-300 px-2.5 py-1 text-[11px] font-semibold text-amber-700 dark:text-amber-200"
                        @click="chooseFixedSource(step, idx)"
                      >
                        我自己选
                      </button>
                    </div>
                  </div>
                </div>

                <div v-if="isUploadStep(step)" class="grid gap-3">
                  <div class="grid gap-2 sm:grid-cols-4">
                    <label class="flex cursor-pointer items-center gap-2 rounded-xl border border-violet-100 dark:border-violet-900/60 bg-white dark:bg-[#272728] px-3 py-2 text-xs font-semibold">
                      <input
                        type="radio"
                        :name="uploadSourceGroupName(step, idx)"
                        value="fixed"
                        class="accent-[#831bd7]"
                        :checked="uploadSource(step).mode === 'fixed' || !uploadSource(step).mode"
                        @change="chooseFixedSource(step, idx, $event)"
                      />
                      固定文件
                    </label>
                    <label class="flex cursor-pointer items-center gap-2 rounded-xl border border-violet-100 dark:border-violet-900/60 bg-white dark:bg-[#272728] px-3 py-2 text-xs font-semibold">
                      <input
                        type="radio"
                        :name="uploadSourceGroupName(step, idx)"
                        value="parameter"
                        class="accent-[#831bd7]"
                        :checked="uploadSource(step).mode === 'parameter'"
                        @change="chooseParameterSource(step, idx)"
                      />
                      运行时参数
                    </label>
                    <label class="flex cursor-pointer items-center gap-2 rounded-xl border border-violet-100 dark:border-violet-900/60 bg-white dark:bg-[#272728] px-3 py-2 text-xs font-semibold">
                      <input
                        type="radio"
                        :name="uploadSourceGroupName(step, idx)"
                        value="path"
                        class="accent-[#831bd7]"
                        :checked="uploadSource(step).mode === 'path'"
                        @change="choosePathSource(step, idx, $event)"
                      />
                      指定路径
                    </label>
                    <label class="flex cursor-pointer items-center gap-2 rounded-xl border border-violet-100 dark:border-violet-900/60 bg-white dark:bg-[#272728] px-3 py-2 text-xs font-semibold">
                      <input
                        type="radio"
                        :name="uploadSourceGroupName(step, idx)"
                        value="dataflow"
                        class="accent-[#831bd7]"
                        :checked="uploadSource(step).mode === 'dataflow'"
                        :disabled="downloadableSteps.length === 0 && !uploadHint(step).source_result_key"
                        @change="chooseDataflowSource(step, idx)"
                      />
                      关联文件产物
                    </label>
                  </div>

                  <div v-if="uploadSource(step).mode === 'parameter'" class="grid gap-2 rounded-xl bg-white dark:bg-[#272728] p-3 ring-1 ring-violet-100 dark:ring-violet-900/60">
                    <label class="text-xs font-bold uppercase tracking-wide text-gray-400 dark:text-gray-500">参数名</label>
                    <input
                      :value="uploadSource(step).param_name || defaultUploadParamName(idx)"
                      class="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-2 text-sm outline-none transition-colors focus:border-[#831bd7]"
                      @change="updateParameterName(step, idx, $event)"
                    />
                    <div class="flex flex-wrap items-center gap-2">
                      <p class="min-w-0 flex-1 truncate text-[11px] text-gray-500 dark:text-gray-400">
                        默认文件: {{ uploadSource(step).default_asset_path || '无，运行时必须提供' }}
                      </p>
                      <button
                        v-if="uploadStaging(step).staging_id || uploadStaging(step).items"
                        type="button"
                        class="rounded-lg border border-violet-200 px-2.5 py-1.5 text-xs font-semibold text-violet-700 dark:text-violet-300"
                        @click="useRecordedFileAsParameterDefault(step, idx)"
                      >
                        使用录制时的文件
                      </button>
                      <label class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-[#831bd7] px-2.5 py-1.5 text-xs font-bold text-white">
                        <Upload :size="13" />
                        上传默认文件
                        <input type="file" class="hidden" @change="uploadFixedAsset(step, idx, $event)" />
                      </label>
                    </div>
                  </div>

                  <div v-else-if="uploadSource(step).mode === 'path'" class="grid gap-2 rounded-xl bg-white dark:bg-[#272728] p-3 ring-1 ring-violet-100 dark:ring-violet-900/60">
                    <label class="text-xs font-bold uppercase tracking-wide text-gray-400 dark:text-gray-500">本机文件路径</label>
                    <input
                      :value="uploadSource(step).path || ''"
                      class="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-2 text-sm outline-none transition-colors focus:border-[#831bd7]"
                      placeholder="/Users/gao/Desktop/采购明细导入模板.xlsx"
                      @change="updatePathSource(step, idx, $event)"
                    />
                  </div>

                  <div v-else-if="uploadSource(step).mode === 'dataflow'" class="grid gap-2 rounded-xl bg-white dark:bg-[#272728] p-3 ring-1 ring-violet-100 dark:ring-violet-900/60">
                    <label class="text-xs font-bold uppercase tracking-wide text-gray-400 dark:text-gray-500">文件产物</label>
                    <select
                      class="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-2 text-sm outline-none transition-colors focus:border-[#831bd7]"
                      :value="uploadSource(step).source_result_key || uploadHint(step).source_result_key || ''"
                      @change="onDataflowSelect(step, idx, $event)"
                    >
                      <option value="">选择文件产物...</option>
                      <option
                        v-for="downloadStep in downloadableSteps"
                        :key="downloadStep.result_key"
                        :value="downloadStep.result_key"
                      >
                        {{ sourceOptionLabel(downloadStep) }}
                      </option>
                    </select>
                  </div>

                  <div v-else class="flex flex-wrap items-center gap-2 rounded-xl bg-white dark:bg-[#272728] p-3 ring-1 ring-violet-100 dark:ring-violet-900/60">
                    <span class="min-w-0 flex-1 truncate text-xs text-gray-600 dark:text-gray-400">
                      {{ uploadSource(step).asset_path || (uploadStaging(step).staging_id || uploadStaging(step).items ? '可使用录制时的文件' : '文件缺失，请重新上传') }}
                    </span>
                    <button
                      v-if="uploadStaging(step).staging_id || uploadStaging(step).items"
                      type="button"
                      class="rounded-lg border border-violet-200 px-2.5 py-1.5 text-xs font-semibold text-violet-700 dark:text-violet-300"
                      :disabled="uploadingStepIndex === idx"
                      @click="promoteRecordedFile(step, idx)"
                    >
                      使用录制时的文件
                    </button>
                    <label class="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-[#831bd7] px-2.5 py-1.5 text-xs font-bold text-white">
                      <Upload :size="13" />
                      {{ uploadingStepIndex === idx ? '上传中...' : '重新上传' }}
                      <input type="file" class="hidden" @change="uploadFixedAsset(step, idx, $event)" />
                    </label>
                  </div>
                </div>
              </article>
            </template>
          </section>
        </section>

        <aside class="space-y-4 xl:sticky xl:top-24 xl:self-start">
          <section class="rounded-3xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] p-5 shadow-sm">
            <div class="flex items-center justify-between gap-3">
              <div class="min-w-0">
                <h2 class="text-base font-extrabold">脚本预览</h2>
                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  {{ scriptGenerating ? '正在生成最终 Skill 脚本...' : (generatedScript ? '已根据录制 trace 生成最终脚本' : '脚本尚未生成') }}
                </p>
              </div>
              <button
                type="button"
                class="shrink-0 rounded-full border border-[#831bd7]/25 px-3 py-1.5 text-xs font-semibold text-[#831bd7] transition-colors hover:bg-[#831bd7]/5 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="!generatedScript || scriptGenerating || hasDiagnostics"
                @click="isScriptDrawerOpen = true"
              >
                查看完整脚本
              </button>
            </div>
            <div
              v-if="hasDiagnostics"
              class="mt-4 rounded-2xl border border-rose-200 dark:border-rose-900/60 bg-rose-50/80 dark:bg-rose-950/20 px-4 py-3 text-xs text-rose-700 dark:text-rose-300"
            >
              还有 {{ diagnostics.length }} 个待修复步骤，脚本预览与测试已暂时阻止。
            </div>
            <pre
              v-if="generatedScript"
              class="mt-4 max-h-56 overflow-auto rounded-2xl bg-[#0f1115] p-3 text-[11px] leading-5 text-emerald-300"
            ><code>{{ generatedScript.slice(0, 1600) }}{{ generatedScript.length > 1600 ? '\n...' : '' }}</code></pre>
          </section>

          <section class="rounded-3xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] p-5 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#f4eaff] text-[#831bd7]">
                <Settings :size="18" />
              </div>
              <div>
                <h2 class="text-base font-extrabold">技能信息</h2>
              </div>
            </div>

            <div class="mt-4 space-y-4">
              <div class="space-y-1.5">
                <label class="text-xs font-semibold text-gray-500 dark:text-gray-400">技能名称</label>
                <input
                  v-model="skillName"
                  class="w-full rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-2.5 text-sm outline-none transition-colors focus:border-[#831bd7] focus:bg-white"
                />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-semibold text-gray-500 dark:text-gray-400">描述</label>
                <textarea
                  v-model="skillDescription"
                  rows="3"
                  class="w-full resize-none rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-3 py-2.5 text-sm outline-none transition-colors focus:border-[#831bd7] focus:bg-white"
                />
              </div>
            </div>
          </section>

          <section class="rounded-3xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] p-5 shadow-sm">
            <div class="flex items-start gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#f4eaff] text-[#831bd7]">
                <Tag :size="18" />
              </div>
              <div class="min-w-0">
                <h2 class="text-base font-extrabold">可配置参数</h2>
              </div>
            </div>

            <div v-if="params.length > 0" class="mt-4 max-h-[calc(100vh-22rem)] space-y-3 overflow-y-auto pr-1">
              <div
                v-for="param in params"
                :key="param.id"
                class="rounded-2xl border border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] p-3"
              >
                <div class="flex items-center gap-3">
                  <input
                    v-model="param.enabled"
                    type="checkbox"
                    class="h-4 w-4 rounded border-gray-300 dark:border-gray-600 accent-[#831bd7]"
                  />
                  <input
                    v-model="param.name"
                    class="min-w-0 flex-1 border-0 bg-transparent text-sm font-semibold text-gray-800 dark:text-gray-200 outline-none"
                    placeholder="参数名"
                  />
                  <span
                    class="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    :class="param.type === 'file' ? 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-300' : param.sensitive ? 'bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-700 dark:text-fuchsia-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300'"
                  >
                    {{ param.type === 'file' ? '文件' : param.sensitive ? '敏感' : '普通' }}
                  </span>
                </div>

                <p class="mt-2 text-[11px] text-gray-500 dark:text-gray-400">{{ param.label }}</p>

                <div class="mt-3">
                  <select
                    v-if="param.sensitive"
                    v-model="param.credential_id"
                    class="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] px-3 py-2 text-sm text-gray-700 dark:text-gray-300 outline-none transition-colors focus:border-[#831bd7]"
                  >
                    <option value="">选择凭据...</option>
                    <option
                      v-for="cred in credentials"
                      :key="cred.id"
                      :value="cred.id"
                    >
                      {{ cred.name || '未命名凭据' }}{{ cred.username ? ` (${cred.username})` : '' }}
                    </option>
                  </select>
                  <div
                    v-else-if="param.type === 'file'"
                    class="rounded-xl border border-violet-100 dark:border-violet-900/60 bg-white dark:bg-[#272728] px-3 py-2 text-xs text-gray-600 dark:text-gray-400"
                  >
                    默认值: {{ param.current_value || '运行时提供' }}
                  </div>
                  <input
                    v-else
                    v-model="param.current_value"
                    class="w-full rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#272728] px-3 py-2 text-sm text-gray-700 dark:text-gray-300 outline-none transition-colors focus:border-[#831bd7]"
                    placeholder="默认值"
                  />
                </div>
              </div>
            </div>

            <div v-else class="mt-4 rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 bg-[#fafafa] dark:bg-[#383739] px-4 py-6 text-center text-sm text-gray-400 dark:text-gray-500">
              当前没有可参数化的输入步骤。
            </div>
          </section>
        </aside>
      </div>
    </main>

    <Dialog :open="isScriptDrawerOpen" @update:open="(open: boolean) => { isScriptDrawerOpen = open }">
      <DialogContent
        class="left-auto right-0 top-0 h-screen w-[min(760px,100vw)] max-h-none max-w-none translate-x-0 translate-y-0 overflow-hidden rounded-none border-l border-gray-200 dark:border-gray-700 bg-[#0f1115] p-0"
      >
        <div class="flex h-full flex-col">
          <DialogHeader class="border-b border-white/10 px-6 py-4 text-left">
            <DialogTitle class="flex items-center gap-2 text-base font-bold text-white">
              <Code :size="18" />
              脚本预览
            </DialogTitle>
          </DialogHeader>

          <div class="flex-1 overflow-auto px-6 py-5">
            <pre class="min-h-full overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs leading-6 text-emerald-300"><code>{{ generatedScript }}</code></pre>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
