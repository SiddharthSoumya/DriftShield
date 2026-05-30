function LiveStatusBadge() {

    return (

        <div
            style={{
                display: "flex",
                alignItems: "center",
                gap: "10px",
                background: "#FFFFFF",
                borderRadius: "12px",
                padding: "10px 18px",
                boxShadow: "0 8px 20px rgba(0,0,0,0.08)",
                width: "fit-content",
                marginBottom: "25px"
            }}
        >

            <div
                style={{
                    width: "10px",
                    height: "10px",
                    borderRadius: "50%",
                    background: "#22C55E",
                    animation: "pulse 1.5s infinite"
                }}
            />

            <span
                style={{
                    color: "#124E66",
                    fontWeight: "600",
                    fontSize: "14px"
                }}
            >
                Live Infrastructure Monitoring Active
            </span>

        </div>
    );
}

export default LiveStatusBadge;