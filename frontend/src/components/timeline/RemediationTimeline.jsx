function RemediationTimeline({ events }) {

    return (

        <div
            style={{
                marginTop: "35px",
                background: "#FFFFFF",
                borderRadius: "20px",
                padding: "25px",
                boxShadow: "0 8px 20px rgba(0,0,0,0.08)"
            }}
        >

            <h2
                style={{
                    color: "#124E66",
                    marginBottom: "20px"
                }}
            >
                Auto-Remediation Activity
            </h2>

            {
                events.length === 0 ? (

                    <p style={{ color: "#748D92" }}>
                        No remediation activity found.
                    </p>

                ) : (

                    events.slice(0, 5).map((event, index) => (

                        <div
                            key={index}
                            style={{
                                borderLeft: "3px solid #124E66",
                                paddingLeft: "15px",
                                marginBottom: "20px"
                            }}
                        >

                            <p
                                style={{
                                    margin: 0,
                                    color: "#212A31",
                                    fontWeight: "600"
                                }}
                            >
                                Drift Detected:
                                {" "}
                                {event.configKey}
                            </p>

                            <p
                                style={{
                                    margin: "5px 0",
                                    color: "#748D92"
                                }}
                            >
                                Expected:
                                {" "}
                                {event.oldValue}
                            </p>

                            <p
                                style={{
                                    margin: "5px 0",
                                    color: "#748D92"
                                }}
                            >
                                Actual:
                                {" "}
                                {event.newValue}
                            </p>

                            <p
                                style={{
                                    margin: "5px 0",
                                    color: event.reverted
                                        ? "#22C55E"
                                        : "#EF4444",
                                    fontWeight: "600"
                                }}
                            >
                                {
                                    event.reverted
                                        ? "Auto-Reverted Successfully"
                                        : "Pending Remediation"
                                }
                            </p>

                        </div>
                    ))

                )
            }

        </div>
    );
}

export default RemediationTimeline;