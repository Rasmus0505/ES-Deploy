import {
  type DeleteOperationResponse,
  type ReadingHistoryResponse,
  type ReadingMaterialGenerateRequest,
  type ReadingMaterialResponse,
  type ReadingShortAnswerHistoryDeleteRequest,
  type ReadingShortAnswerHistoryResponse,
  type ReadingShortAnswerSubmitRequest,
  type ReadingShortAnswerSubmitResponse,
  type ReadingSourcesResponse,
  type ReadingVersionResponse
} from '../../types/backend';
import { requestJson } from './http';

export async function fetchReadingSources() {
  return requestJson<ReadingSourcesResponse>('/reading/sources', { retry: 0 });
}

export async function fetchReadingMaterial(params: {
  video_name?: string;
  srt_name?: string;
  user_level?: string;
  version_id?: string;
}) {
  const query = new URLSearchParams();
  const safeVersionId = String(params.version_id || '').trim();
  if (safeVersionId) {
    query.set('version_id', safeVersionId);
  } else {
    query.set('video_name', String(params.video_name || '').trim());
    query.set('srt_name', String(params.srt_name || '').trim());
    query.set('user_level', String(params.user_level || '').trim().toLowerCase() || 'cet4');
  }
  return requestJson<ReadingMaterialResponse>(`/reading/materials?${query.toString()}`, { retry: 0 });
}

export async function fetchReadingHistory(limit = 20, offset = 0) {
  const query = new URLSearchParams({
    limit: String(Math.max(1, Math.min(100, Number(limit) || 20))),
    offset: String(Math.max(0, Number(offset) || 0))
  });
  return requestJson<ReadingHistoryResponse>(`/reading/history?${query.toString()}`, { retry: 0 });
}

export async function fetchReadingVersion(versionId: string) {
  return requestJson<ReadingVersionResponse>(`/reading/versions/${encodeURIComponent(String(versionId || '').trim())}`, { retry: 0 });
}

export async function deleteReadingVersion(versionId: string) {
  return requestJson<DeleteOperationResponse>(`/reading/versions/${encodeURIComponent(String(versionId || '').trim())}`, {
    method: 'DELETE',
    retry: 0
  });
}

export async function generateReadingMaterial(payload: ReadingMaterialGenerateRequest) {
  return requestJson<ReadingMaterialResponse>('/reading/materials/generate', {
    method: 'POST',
    body: {
      ...payload,
      user_level: String(payload.user_level || '').trim().toLowerCase() || 'cet4',
      scope: payload.scope || 'all',
      ratio_preset: payload.ratio_preset || 'long_term',
      difficulty_tier: payload.difficulty_tier || 'balanced',
      genre: payload.genre || 'news',
      force_regenerate: Boolean(payload.force_regenerate)
    },
    retry: 0
  });
}

export async function submitReadingShortAnswer(payload: ReadingShortAnswerSubmitRequest) {
  return requestJson<ReadingShortAnswerSubmitResponse>('/reading/quiz/short-answers/submit', {
    method: 'POST',
    body: payload,
    retry: 0
  });
}

export async function fetchReadingShortAnswerHistory(params: { version_id: string; question_id?: string; limit?: number }) {
  const query = new URLSearchParams({
    version_id: String(params.version_id || '').trim(),
    limit: String(Math.max(1, Math.min(500, Number(params.limit) || 100)))
  });
  const questionId = String(params.question_id || '').trim();
  if (questionId) query.set('question_id', questionId);
  return requestJson<ReadingShortAnswerHistoryResponse>(`/reading/quiz/short-answers/history?${query.toString()}`, { retry: 0 });
}

export async function deleteReadingShortAnswerGroup(payload: ReadingShortAnswerHistoryDeleteRequest) {
  return requestJson<DeleteOperationResponse>('/reading/quiz/short-answers/history/group', {
    method: 'DELETE',
    body: payload,
    retry: 0
  });
}
