// Hand-written for now; to be replaced by types generated from the OpenAPI schema (`npm run gen:api`).
export interface HealthResponse {
  status: string;
  version: string;
  database: string;
}
