const BASE_URLS = {
  prod: 'https://healthygatorsportfan-ab9271b02569.herokuapp.com',
  dev: 'http://127.0.0.1:8000',
};

// Switch between 'dev' and 'prod' here
const ACTIVE_ENV: keyof typeof BASE_URLS = 'prod';

export const BASE_URL = BASE_URLS[ACTIVE_ENV];
export const ENV = ACTIVE_ENV;
export const API_KEY = process.env.EXPO_PUBLIC_API_KEY ?? '';
