FROM eclipse-temurin:21

WORKDIR /app

COPY target/backend-0.0.1-SNAPSHOT.jar app.jar

COPY driftshield-data ./driftshield-data

ENTRYPOINT ["java","-jar","app.jar"]