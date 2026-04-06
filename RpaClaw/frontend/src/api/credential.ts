import { apiClient } from './client';

export interface Credential {
  id: string;
  name: string;
  username: string;
  domain: string;
  created_at: string;
  updated_at: string;
}

export interface CredentialCreate {
  name: string;
  username: string;
  password: string;
  domain?: string;
}

export interface CredentialUpdate {
  name?: string;
  username?: string;
  password?: string;
  domain?: string;
}

export async function listCredentials(): Promise<Credential[]> {
  const resp = await apiClient.get('/credentials');
  return resp.data.credentials;
}

export async function createCredential(data: CredentialCreate): Promise<Credential> {
  const resp = await apiClient.post('/credentials', data);
  return resp.data.credential;
}

export async function updateCredential(id: string, data: CredentialUpdate): Promise<Credential> {
  const resp = await apiClient.put(`/credentials/${id}`, data);
  return resp.data.credential;
}

export async function deleteCredential(id: string): Promise<void> {
  await apiClient.delete(`/credentials/${id}`);
}
