import { ref } from 'vue'
import type { FileInfo } from '../api/file'
import { eventBus } from '../utils/eventBus'
import { EVENT_SHOW_FILE_PANEL } from '../constants/event'

const isShow = ref(false)
const visible = ref(true)
const fileInfo = ref<FileInfo>()
const isListMode = ref(false)

export function useFilePanel() {
  const showFilePanel = (file: FileInfo) => {
    eventBus.emit(EVENT_SHOW_FILE_PANEL)
    visible.value = true
    fileInfo.value = file
    isListMode.value = false
    isShow.value = true
  }

  const showFileListPanel = () => {
    eventBus.emit(EVENT_SHOW_FILE_PANEL)
    visible.value = true
    isListMode.value = true
    isShow.value = true
  }

  const hideFilePanel = () => {
    isShow.value = false
  }

  return {
    isShow,
    fileInfo,
    visible,
    isListMode,
    showFilePanel,
    showFileListPanel,
    hideFilePanel
  }
} 