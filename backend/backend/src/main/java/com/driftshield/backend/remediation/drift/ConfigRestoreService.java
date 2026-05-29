package com.driftshield.backend.remediation.drift;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.yaml.snakeyaml.Yaml;

import java.io.FileWriter;
import java.nio.file.Path;
import java.util.Map;

@Service
@Slf4j
public class ConfigRestoreService {

    private static final String RUNTIME_PATH =
            "/app/driftshield-data/runtime/runtime.yml";

    public void restoreRuntimeConfig(
            Map<String,Object> baselineConfig
    ){
        try{
            Yaml yaml = new Yaml();
            Path runtimePath= Path.of(
                    RUNTIME_PATH
            );
            log.info("Runtime path = {}", runtimePath.toAbsolutePath());

            FileWriter writer=
                    new FileWriter(runtimePath.toFile());
            yaml.dump(baselineConfig,writer);

            writer.close();

            log.info("Runtime config restored successfully");
        }
        catch (Exception e){

            log.error(
                    "Restore failed",e
            );

            throw new RuntimeException(
                    "Failed to restore runtime config",e
            );
        }
    }
}
