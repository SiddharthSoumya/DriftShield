package com.driftshield.backend.dto.response;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;

@Getter
@AllArgsConstructor
@Builder
public class DriftDifference {
    private String configKey;
    private Object baselineValue;
    private Object runtimeValue;

}
