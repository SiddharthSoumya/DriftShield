import axios from "axios";

const BASE_URL = "http://driftshield-backend:8080";

export const getAllDriftEvents = async () => {

    const response = await axios.get(
        `${BASE_URL}/api/drift/events`
    );

    return response.data;
};

export const getRuntimeConfig = async () => {

    const response = await axios.get(
        `${BASE_URL}/api/runtime-config`
    );

    return response.data;
};