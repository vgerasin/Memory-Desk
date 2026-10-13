import { useMemo } from 'react'
import { api, errorText } from './api'
import { useApp } from './store'
import type { DecisionDto, TaskDto } from './types'
import { tr } from './i18n'

export function useActions() {
  const { invalidate, showToast } = useApp()
  return useMemo(() => {
    const safe = async (fn: () => Promise<unknown>, ok?: string): Promise<boolean> => {
      try {
        const result = (await fn()) as { noteSynced?: boolean } | null | undefined
        invalidate()
        if (result && result.noteSynced === false) showToast(tr('Изменено в кабинете, но строку в заметке Obsidian найти не удалось — возможно, заметку правили вручную'))
        else if (ok) showToast(ok)
        return true
      } catch (e) { showToast(errorText(e)); return false }
    }
    return {
      toggleTask: (t: TaskDto) => safe(() => api.patch(`/api/tasks/${t.id}`, { status: t.done ? 'open' : 'done' })),
      confirmTask: (t: TaskDto) => safe(() => api.patch(`/api/tasks/${t.id}`, { status: 'open' }), tr('Задача подтверждена')),
      rejectTask: (t: TaskDto) => safe(() => api.patch(`/api/tasks/${t.id}`, { status: 'rejected' }),
        tr('Задача отклонена')),
      confirmDecision: (d: DecisionDto) => safe(() => api.patch(`/api/decisions/${d.id}`, { status: 'confirmed' }), tr('Решение подтверждено')),
      rejectDecision: (d: DecisionDto) => safe(() => api.patch(`/api/decisions/${d.id}`, { status: 'rejected' }), tr('Решение отклонено')),
      editTask: (id: string, patch: { text?: string; owner?: string; deadline?: string }) =>
        safe(() => api.patch(`/api/tasks/${id}`, patch), tr('Задача обновлена')),
      editDecision: (id: string, patch: { text?: string }) =>
        safe(() => api.patch(`/api/decisions/${id}`, patch), tr('Решение обновлено')),
      publish: (id: string) => safe(() => api.post(`/api/meetings/${id}/publish`), tr('Заметка опубликована в Obsidian')),
      safe,
    }
  }, [invalidate, showToast])
}
