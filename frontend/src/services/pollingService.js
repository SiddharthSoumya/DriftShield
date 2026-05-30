export const startPolling = (
    callback,
    interval = 5000
) => {

    callback();

    return setInterval(
        callback,
        interval
    );
};