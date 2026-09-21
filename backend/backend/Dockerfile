FROM maven:3.9.9-eclipse-temurin-21 AS build

WORKDIR /src

COPY . .
RUN chmod +x mvnw && ./mvnw -B -DskipTests clean package

FROM eclipse-temurin:21-jre

WORKDIR /app

COPY --from=build /src/target/backend-0.0.1-SNAPSHOT.jar app.jar
COPY --from=build /src/driftshield-data ./driftshield-data

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["sh", "-c", "java -jar app.jar --server.port=${PORT}"]