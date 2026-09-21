import axios from "axios";

const API_BASE_URL =
    import.meta.env.VITE_API_BASE_URL ||
    (window.location.hostname === "localhost"
        ? "http://localhost:8080"
        : window.location.origin);

const buildUrl = (path) => {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    return `${API_BASE_URL}${normalizedPath}`;
};

export const getAllDriftEvents = async () => {
    const response = await axios.get(buildUrl("/api/drift/events"));
    return response.data;
};

export const getRuntimeConfig = async () => {
    const response = await axios.get(buildUrl("/api/runtime-config"));
    return response.data;
};