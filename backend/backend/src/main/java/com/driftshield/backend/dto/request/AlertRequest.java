package com.driftshield.backend.dto.request;


import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class AlertRequest {
    private String type;
    private String severity;
    private String message;

}
