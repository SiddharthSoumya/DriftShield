package com.driftshield.backend.detection.drift;

import com.driftshield.backend.dto.response.DriftDifference;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Component
public class ConfigComparator {

    private static final String RUNTIME_PATH =
            "driftshield-data/runtime/runtime.yml";

    private static final String BASELINE_PATH =
            "driftshield-data/baseline/baseline.yml";


    public List<DriftDifference> compareConfigs(
            Map<String, Object> baselineConfig,
            Map<String, Object> runtimeConfig
    ) {

        List<DriftDifference> differences =
                new ArrayList<>();

        for (String key : baselineConfig.keySet()) {

            Object baselineValue =
                    baselineConfig.get(key);

            Object runtimeValue =
                    runtimeConfig.get(key);

            String baselineNormalized =
                    String.valueOf(baselineValue).trim();

            String runtimeNormalized =
                    String.valueOf(runtimeValue).trim();

            System.out.println(
                    "BASELINE = " + baselineNormalized +
                            " | RUNTIME = " + runtimeNormalized
            );

            // ONLY ADD DIFFERENCE IF VALUES DO NOT MATCH
            if (!baselineNormalized.equals(runtimeNormalized)) {

                DriftDifference difference =
                        DriftDifference.builder()
                                .configKey(key)
                                .baselineValue(baselineNormalized)
                                .runtimeValue(runtimeNormalized)
                                .build();

                differences.add(difference);
            }
        }

        return differences;
    }
}