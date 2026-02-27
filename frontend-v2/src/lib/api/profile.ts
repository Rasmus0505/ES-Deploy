import type { ProfileSettings, ProfileSettingsUpdateRequest } from '../../types/backend';
import { requestJson } from './http';

export async function fetchProfileSettings() {
  return requestJson<ProfileSettings>('/profile/settings', { retry: 0 });
}

export async function updateProfileSettings(payload: ProfileSettingsUpdateRequest) {
  return requestJson<ProfileSettings>('/profile/settings', {
    method: 'PUT',
    body: payload,
    retry: 0
  });
}
