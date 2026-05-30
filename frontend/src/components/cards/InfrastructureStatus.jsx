function InfrastructureStatus({ config }) {

    if (!config) {

        return <p>Loading infrastructure status...</p>;
    }

    return (

        <div
            style={{
                display: "flex",
                flexDirection: "column",
                gap: "18px"
            }}
        >

            <StatusItem
                label="Firewall"
                value={
                    config.firewall_enabled
                        ? "ENABLED"
                        : "DISABLED"
                }
            />

            <StatusItem
                label="Public Access"
                value={
                    config.public_access
                        ? "OPEN"
                        : "BLOCKED"
                }
            />

            <StatusItem
                label="SSH Port"
                value={config.ssh_port}
            />

            <StatusItem
                label="Admin Endpoint"
                value={
                    config.admin_endpoint_exposed
                        ? "EXPOSED"
                        : "SECURED"
                }
            />

            <StatusItem
                label="Rate Limit"
                value={config.max_requests_per_minute}
            />

        </div>
    );
}

function StatusItem({

                        label,
                        value

                    }) {

    return (

        <div
            style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "16px 20px",
                background: "#F8FAFC",
                borderRadius: "14px",
                border:
                    "1px solid rgba(18, 78, 102, 0.08)"
            }}
        >

            <span
                style={{
                    fontWeight: "600",
                    color: "#124E66"
                }}
            >
                {label}
            </span>

            <span
                style={{
                    color: "#0F172A",
                    fontWeight: "700"
                }}
            >
                {value}
            </span>

        </div>
    );
}

export default InfrastructureStatus;