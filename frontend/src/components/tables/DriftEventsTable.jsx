function DriftEventsTable({ events }) {

    const getSeverityColor = (severity) => {

        switch (severity) {

            case "HIGH":
                return "#EF4444";

            case "MEDIUM":
                return "#F59E0B";

            default:
                return "#22C55E";
        }
    };

    return (

        <div
            style={{
                marginTop: "35px",
                background: "#FFFFFF",
                borderRadius: "20px",
                padding: "25px",
                boxShadow: "0 8px 20px rgba(0,0,0,0.08)",
                overflowX: "auto"
            }}
        >

            <h2
                style={{
                    color: "#124E66",
                    marginBottom: "20px"
                }}
            >
                Drift Events
            </h2>

            {
                events.length === 0 ? (

                    <p style={{ color: "#748D92" }}>
                        No drift events detected.
                    </p>

                ) : (

                    <table
                        style={{
                            width: "100%",
                            borderCollapse: "collapse"
                        }}
                    >

                        <thead>

                        <tr
                            style={{
                                background: "#F8FAFC"
                            }}
                        >

                            <th style={tableHeader}>
                                Field
                            </th>

                            <th style={tableHeader}>
                                Expected
                            </th>

                            <th style={tableHeader}>
                                Actual
                            </th>

                            <th style={tableHeader}>
                                Severity
                            </th>

                            <th style={tableHeader}>
                                Reverted
                            </th>

                        </tr>

                        </thead>

                        <tbody>

                        {
                            events.map((event, index) => (

                                <tr key={index}>

                                    <td style={tableCell}>
                                        {event.configKey}
                                    </td>

                                    <td style={tableCell}>
                                        {event.oldValue}
                                    </td>

                                    <td style={tableCell}>
                                        {event.newValue}
                                    </td>

                                    <td style={tableCell}>

                                        <span
                                            style={{
                                                background:
                                                    getSeverityColor(
                                                        event.severity
                                                    ),
                                                color: "white",
                                                padding: "6px 12px",
                                                borderRadius: "30px",
                                                fontSize: "12px",
                                                fontWeight: "600"
                                            }}
                                        >
                                            {event.severity}
                                        </span>

                                    </td>

                                    <td style={tableCell}>
                                        {
                                            event.reverted
                                                ? "YES"
                                                : "NO"
                                        }
                                    </td>

                                </tr>
                            ))
                        }

                        </tbody>

                    </table>

                )
            }

        </div>
    );
}

const tableHeader = {

    padding: "15px",
    textAlign: "left",
    color: "#124E66",
    fontWeight: "700"
};

const tableCell = {

    padding: "15px",
    color: "#212A31",
    borderTop: "1px solid #E2E8F0"
};

export default DriftEventsTable;