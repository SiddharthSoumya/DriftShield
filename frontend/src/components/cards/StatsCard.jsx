function StatsCard({

                       title,
                       value

                   }) {

    return (

        <div
            style={{

                background: "#FFFFFF",

                borderRadius: "20px",

                padding: "24px",

                border:
                    "1px solid rgba(18, 78, 102, 0.08)",

                boxShadow:
                    "0 8px 20px rgba(15, 23, 42, 0.08), 0 2px 6px rgba(15, 23, 42, 0.04)",

                transition:
                    "all 0.25s ease-in-out",

                cursor: "pointer"
            }}

            onMouseEnter={(e) => {

                e.currentTarget.style.transform =
                    "translateY(-4px)";

                e.currentTarget.style.boxShadow =
                    "0 14px 30px rgba(15, 23, 42, 0.12)";
            }}

            onMouseLeave={(e) => {

                e.currentTarget.style.transform =
                    "translateY(0px)";

                e.currentTarget.style.boxShadow =
                    "0 8px 20px rgba(15, 23, 42, 0.08), 0 2px 6px rgba(15, 23, 42, 0.04)";
            }}
        >

            <p
                style={{
                    color: "#748D92",
                    fontSize: "15px",
                    fontWeight: "600",
                    marginBottom: "12px",
                    letterSpacing: "0.5px"
                }}
            >
                {title}
            </p>

            <h1
                style={{
                    color: "#124E66",
                    fontSize: "38px",
                    fontWeight: "800",
                    margin: 0
                }}
            >
                {value}
            </h1>

        </div>
    );
}

export default StatsCard;