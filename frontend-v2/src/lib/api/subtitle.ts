import {
  type HealthResponse,
  type HistoryRecord,
  type HistoryRecordsResponse,
  type HistoryRecordsSyncResponse,
  type JobCreateResponse,
  type JobStatusResponse,
  type SubtitleJobFromUrlRequest,
  type SubtitleJobOptionsPayload,
  type SubtitleJobResult,
  type WhisperLocalModelsResponse
} from '../../types/backend';
import { requestBlob, requestJson } from './http';

export async function fetchHealth() {
  return requestJson<HealthResponse>('/health');
}

export async function fetchWhisperLocalModels() {
  return requestJson<WhisperLocalModelsResponse>('/whisper/local-models', { retry: 0 });
}

export async function createSubtitleJobFromFile(videoFile: File, options: SubtitleJobOptionsPayload) {
  const form = new FormData();
  form.append('video_file', videoFile);
  form.append('options_json', JSON.stringify(options));
  return requestJson<JobCreateResponse>('/subtitle-jobs', {
    method: 'POST',
    body: form,
    retry: 0
  });
}

export async function createSubtitleJobFromUrl(payload: SubtitleJobFromUrlRequest) {
  return requestJson<JobCreateResponse>('/subtitle-jobs/from-url', {
    method: 'POST',
    body: payload,
    retry: 0
  });
}

export async function fetchSubtitleJobStatus(jobId: string) {
  return requestJson<JobStatusResponse>(`/subtitle-jobs/${encodeURIComponent(jobId)}`, {
    retry: 0
  });
}

export async function fetchSubtitleJobResult(jobId: string) {
  return requestJson<SubtitleJobResult>(`/subtitle-jobs/${encodeURIComponent(jobId)}/result`, {
    retry: 0
  });
}

export async function cancelSubtitleJob(jobId: string) {
  return requestJson<{ job_id: string; status: string; cancel_requested: boolean }>(
    `/subtitle-jobs/${encodeURIComponent(jobId)}`,
    {
      method: 'DELETE',
      retry: 0
    }
  );
}

export async function fetchSubtitleJobVideoBlob(jobId: string) {
  return requestBlob(`/subtitle-jobs/${encodeURIComponent(jobId)}/video`, {
    retry: 0
  });
}

export async function fetchHistoryRecords() {
  return requestJson<HistoryRecordsResponse>('/history-records', { retry: 0 });
}

export async function syncHistoryRecords(records: HistoryRecord[]) {
  return requestJson<HistoryRecordsSyncResponse>('/history-records', {
    method: 'PUT',
    body: { records },
    retry: 0
  });
}
