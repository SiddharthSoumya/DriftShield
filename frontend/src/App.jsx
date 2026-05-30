import { useEffect, useState } from "react";

import DriftEventsTable from "./components/tables/DriftEventsTable";
import InfrastructureStatus from "./components/cards/InfrastructureStatus";
import StatsCard from "./components/cards/StatsCard";
import LiveStatusBadge from "./components/badges/LiveStatusBadge";
import RemediationTimeline from "./components/timeline/RemediationTimeline";

import {
    getAllDriftEvents,
    getRuntimeConfig
} from "./api/driftApi";

import { startPolling } from "./services/pollingService";

function App() {

    const [events, setEvents] = useState([]);
    const [runtimeConfig, setRuntimeConfig] = useState(null);

    const totalEvents = events.length;

    const criticalEvents =
        events.filter(
            event => event.severity === "HIGH"
        ).length;

    const revertedEvents =
        events.filter(
            event => event.reverted === true
        ).length;

    const fetchEvents = async () => {

        try {

            const data =
                await getAllDriftEvents();

            setEvents(data);

            const runtime =
                await getRuntimeConfig();

            setRuntimeConfig(runtime);

        } catch (error) {

            console.error(
                "Failed to fetch drift events",
                error
            );
        }
    };

    useEffect(() => {

        fetchEvents();

        const polling =
            startPolling(fetchEvents, 5000);

        return () => clearInterval(polling);

    }, []);

    return (

        <div
            style={{
                minHeight: "100vh",
                background: "#F8FAFC",
                padding: "35px",
                fontFamily: "Inter, sans-serif",
                color: "#0F172A"
            }}
        >

            {/* HEADER */}

            <div
                style={{
                    marginBottom: "35px"
                }}
            >

                <h1
                    style={{
                        fontSize: "38px",
                        fontWeight: "800",
                        marginBottom: "12px",
                        color: "#0F172A",
                        letterSpacing: "-1px"
                    }}
                >
                    DriftShield Dashboard
                </h1>

                <p
                    style={{
                        fontSize: "16px",
                        color: "#475569",
                        maxWidth: "700px",
                        lineHeight: "1.7"
                    }}
                >
                    Autonomous Infrastructure Drift Detection &
                    Auto-Remediation Platform for secure and
                    resilient cloud environments.
                </p>

                <LiveStatusBadge />

            </div>

            {/* STATS SECTION */}

            <div
                style={{
                    display: "grid",
                    gridTemplateColumns:
                        "repeat(auto-fit, minmax(250px, 1fr))",
                    gap: "24px",
                    marginBottom: "35px"
                }}
            >

                <div
                    style={cardStyle}
                >
                    <StatsCard
                        title="Total Drift Events"
                        value={totalEvents}
                    />
                </div>

                <div
                    style={cardStyle}
                >
                    <StatsCard
                        title="Critical Issues"
                        value={criticalEvents}
                    />
                </div>

                <div
                    style={cardStyle}
                >
                    <StatsCard
                        title="Auto-Reverted"
                        value={revertedEvents}
                    />
                </div>

                <div
                    style={cardStyle}
                >
                    <StatsCard
                        title="Infrastructure Status"
                        value="SECURED"
                    />
                </div>

            </div>

            {/* INFRA STATUS */}

            <div
                style={{
                    ...cardStyle,
                    marginBottom: "35px",
                    padding: "25px"
                }}
            >

                <h2
                    style={{
                        marginBottom: "20px",
                        color: "#124E66",
                        fontSize: "24px",
                        fontWeight: "700"
                    }}
                >
                    Runtime Infrastructure Status
                </h2>

                <InfrastructureStatus
                    config={runtimeConfig}
                />

            </div>

            {/* DRIFT EVENTS TABLE */}

            <div
                style={{
                    ...cardStyle,
                    padding: "25px"
                }}
            >

                <h2
                    style={{
                        marginBottom: "20px",
                        color: "#124E66",
                        fontSize: "24px",
                        fontWeight: "700"
                    }}
                >
                    Drift Event Logs
                </h2>

                <DriftEventsTable
                    events={events}
                />

                <RemediationTimeline
                    events={events}
                />

            </div>

        </div>
    );
}

/* MODERN CARD THEME */

const cardStyle = {

    background: "#FFFFFF",

    borderRadius: "22px",

    border: "1px solid rgba(18, 78, 102, 0.08)",

    boxShadow:
        "0 8px 20px rgba(15, 23, 42, 0.08), 0 2px 6px rgba(15, 23, 42, 0.04)",

    transition:
        "all 0.25s ease-in-out",

    transform: "translateY(0px)"
};

export default App;