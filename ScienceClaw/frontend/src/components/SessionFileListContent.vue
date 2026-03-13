<template>
    <div v-if="files.length > 0" class="flex-1 min-h-0 overflow-auto px-3 py-3">
        <div class="space-y-1">
            <div v-for="(file, idx) in files" :key="file.file_id"
                class="file-card group flex items-center gap-3 p-3 rounded-xl cursor-pointer transition-all duration-200 hover:bg-gray-50/80 dark:hover:bg-white/5 border border-transparent hover:border-gray-100 dark:hover:border-gray-800"
                :style="{ '--delay': `${idx * 30}ms` }"
                @click="onFileClick(file)">

                <!-- File icon with type-based color -->
                <div class="size-10 rounded-xl flex items-center justify-center flex-shrink-0 shadow-sm transition-transform duration-200 group-hover:scale-105"
                    :class="getFileColor(file.filename)">
                    <component :is="getFileType(file.filename).icon" class="size-5" />
                </div>

                <!-- File info -->
                <div class="flex flex-col flex-1 min-w-0">
                    <span class="text-sm font-medium text-[var(--text-primary)] truncate group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">{{ file.filename }}</span>
                    <span class="text-xs text-[var(--text-tertiary)] mt-0.5">{{ formatRelativeTime(parseISODateTime(file.upload_date)) }}</span>
                </div>

                <!-- Actions -->
                <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button @click.stop="downloadFile(file)"
                        class="p-2 rounded-xl text-[var(--text-tertiary)] hover:bg-white dark:hover:bg-gray-800 hover:text-blue-500 hover:shadow-sm transition-all duration-200"
                        :title="$t('Download')">
                        <Download class="size-4" />
                    </button>
                </div>
            </div>
        </div>
    </div>
    <div v-else class="flex-1 min-h-0 flex flex-col items-center justify-center gap-4 py-16">
        <div class="size-16 rounded-2xl bg-gray-50 dark:bg-gray-800 flex items-center justify-center">
            <FolderOpen class="size-7 text-gray-300 dark:text-gray-600" />
        </div>
        <div class="text-center">
            <p class="text-sm font-medium text-[var(--text-tertiary)]">{{ $t('No files yet') }}</p>
            <p class="text-xs text-[var(--text-tertiary)] opacity-60 mt-1">{{ $t('Files created during the task will appear here') }}</p>
        </div>
    </div>
</template>

<script setup lang="ts">
import { Download, FolderOpen } from 'lucide-vue-next';
import { ref, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import type { FileInfo } from '../api/file';
import { triggerAuthenticatedDownload } from '../api/file';
import { getSessionFiles, getSharedSessionFiles } from '../api/agent';
import { formatRelativeTime, parseISODateTime } from '../utils/time';
import { getFileType } from '../utils/fileType';
import { useSessionFileList } from '../composables/useSessionFileList';

const emit = defineEmits<{
  (e: 'file-click', file: FileInfo): void
}>();

const route = useRoute();
const files = ref<FileInfo[]>([]);
const { shared } = useSessionFileList();

// Get file color class based on extension
const getFileColor = (filename: string) => {
    const ext = filename.split('.').pop()?.toLowerCase() || '';
    if (['py', 'js', 'ts', 'jsx', 'tsx', 'vue'].includes(ext)) return 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400';
    if (['md', 'txt', 'log', 'csv'].includes(ext)) return 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400';
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'].includes(ext)) return 'bg-violet-50 dark:bg-violet-900/20 text-violet-600 dark:text-violet-400';
    if (['pdf', 'doc', 'docx', 'ppt', 'pptx'].includes(ext)) return 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400';
    if (['json', 'xml', 'yaml', 'yml'].includes(ext)) return 'bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400';
    if (['html', 'css', 'scss'].includes(ext)) return 'bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400';
    if (['xls', 'xlsx'].includes(ext)) return 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400';
    if (['mp4', 'avi', 'mov', 'webm'].includes(ext)) return 'bg-pink-50 dark:bg-pink-900/20 text-pink-600 dark:text-pink-400';
    if (['mp3', 'wav', 'flac', 'aac'].includes(ext)) return 'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400';
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'bg-yellow-50 dark:bg-yellow-900/20 text-yellow-600 dark:text-yellow-400';
    return 'bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400';
};

const fetchFiles = async (sessionId: string) => {
    if (!sessionId) {
        return;
    }
    let response: FileInfo[] = [];
    try {
        if (shared.value) {
            response = await getSharedSessionFiles(sessionId);
        } else {
            response = await getSessionFiles(sessionId);
        }
        files.value = response;
    } catch (e) {
        console.error("fetch files error", e)
    }
}

const downloadFile = async (fileInfo: FileInfo) => {
    try {
        await triggerAuthenticatedDownload(fileInfo);
    } catch (err) {
        console.error('Download failed:', err);
    }
}

const onFileClick = (file: FileInfo) => {
    console.log('SessionFileListContent: file clicked', file);
    emit('file-click', file);
}

onMounted(() => {
    const sessionId = route.params.sessionId as string;
    if (sessionId) {
        fetchFiles(sessionId);
    }
});
</script>

<style scoped>
.file-card {
    animation: fileSlideIn 0.25s ease-out both;
    animation-delay: var(--delay, 0ms);
}
@keyframes fileSlideIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
}
</style>
