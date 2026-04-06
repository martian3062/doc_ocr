import axios from 'axios';

function resolveApiBaseUrl() {
  const configured = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (configured) {
    return configured.replace(/\/+$/, '');
  }

  return '/api/v1';
}

const API_BASE_URL = resolveApiBaseUrl();

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const getDashboardData = () => api.get('/dashboard/');
export const getDocumentList = (params?: { q?: string }) => api.get('/documents/', { params });
export const getPatientList = (params?: { q?: string }) => api.get('/patients/', { params });
export const getPatientDetail = (id: string | number) => api.get(`/patients/${id}/`);
export const getKnowledgeMap = (id: string | number) => api.get(`/patients/${id}/knowledge-map/`);
export const getRunHistory = () => api.get('/runs/');
export const getRunDetail = (id: string) => api.get(`/runs/${id}/`);

export default api;
