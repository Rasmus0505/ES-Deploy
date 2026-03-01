import type {
  AsrConsoleResponse,
  WalletPacksResponse,
  WalletQuotaResponse,
  WalletRedeemRequest,
  WalletRedeemResponse
} from '../../types/backend';
import { requestJson } from './http';

export async function fetchWalletQuota() {
  return requestJson<WalletQuotaResponse>('/wallet/quota', { retry: 0 });
}

export async function fetchWalletPacks() {
  return requestJson<WalletPacksResponse>('/wallet/packs', { retry: 0 });
}

export async function redeemWalletCode(payload: WalletRedeemRequest) {
  return requestJson<WalletRedeemResponse>('/wallet/redeem', {
    method: 'POST',
    body: payload,
    retry: 0
  });
}

export async function fetchAsrConsole(limit = 20) {
  const safeLimit = Math.max(1, Math.min(200, Number(limit || 20)));
  return requestJson<AsrConsoleResponse>(`/asr/console?limit=${safeLimit}`, { retry: 0 });
}
